// MmcHarness: a headless console front end over MMC Editor's own Frosty libraries.
//
// Build:   bash tools/mmc_harness/build.sh   (C# 5, csc.exe from .NET Framework 4.x, x64)
// Output:  C:\Madden27Modding\MuHarness\MmcHarness.exe (outside the repo)
//
// Every MMC DLL is loaded at run time from the editor folder (env MU_MMC_EDITOR_DIR) by an
// AssemblyResolve handler. No MMC file is ever copied.
//
// Each command prints exactly one JSON object on stdout ({"ok": true, ...} or
// {"ok": false, "error": "..."}); everything else (SDK logging, progress) goes to stderr.
// Exit code 0 means ok.
//
// NEVER CALL: FrostyModCryptor (any member), FrostyModKeyProvider (any member),
// FrostyModReader.GetResourceData, or anything else that decrypts mods. This program only
// builds our own mods; it never reads or decrypts anyone else's.
using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Runtime.Serialization;
using System.Text;
using FrostySdk;
using FrostySdk.Interfaces;
using FrostySdk.IO;
using FrostySdk.Managers;
using FrostySdk.Managers.Entries;
using FrostySdk.Resources;

namespace MuHarness
{
    // ------------------------------------------------------------------ JSON

    /// <summary>Ordered JSON object.</summary>
    class JObj : List<KeyValuePair<string, object>>
    {
        public JObj Set(string key, object value)
        {
            for (int i = 0; i < Count; i++)
                if (this[i].Key == key) { this[i] = new KeyValuePair<string, object>(key, value); return this; }
            Add(new KeyValuePair<string, object>(key, value));
            return this;
        }
    }

    static class Json
    {
        public static string Write(object v, bool pretty)
        {
            var sb = new StringBuilder();
            WriteValue(sb, v, pretty, 0);
            return sb.ToString();
        }

        static void Indent(StringBuilder sb, bool pretty, int level)
        {
            if (!pretty) return;
            sb.Append('\n');
            sb.Append(' ', level * 2);
        }

        static void WriteValue(StringBuilder sb, object v, bool pretty, int level)
        {
            if (v == null) { sb.Append("null"); return; }
            if (v is string) { WriteString(sb, (string)v); return; }
            if (v is bool) { sb.Append((bool)v ? "true" : "false"); return; }
            if (v is float || v is double)
            {
                double d = Convert.ToDouble(v, CultureInfo.InvariantCulture);
                if (double.IsNaN(d) || double.IsInfinity(d)) { WriteString(sb, d.ToString(CultureInfo.InvariantCulture)); return; }
                sb.Append(v is float ? ((float)v).ToString("R", CultureInfo.InvariantCulture) : d.ToString("R", CultureInfo.InvariantCulture));
                return;
            }
            if (v is sbyte || v is byte || v is short || v is ushort || v is int || v is uint || v is long || v is ulong || v is decimal)
            {
                sb.Append(Convert.ToString(v, CultureInfo.InvariantCulture));
                return;
            }
            var obj = v as JObj;
            if (obj != null)
            {
                if (obj.Count == 0) { sb.Append("{}"); return; }
                sb.Append('{');
                for (int i = 0; i < obj.Count; i++)
                {
                    if (i > 0) sb.Append(',');
                    Indent(sb, pretty, level + 1);
                    WriteString(sb, obj[i].Key);
                    sb.Append(pretty ? ": " : ":");
                    WriteValue(sb, obj[i].Value, pretty, level + 1);
                }
                Indent(sb, pretty, level);
                sb.Append('}');
                return;
            }
            var en = v as IEnumerable;
            if (en != null)
            {
                var items = en.Cast<object>().ToList();
                if (items.Count == 0) { sb.Append("[]"); return; }
                sb.Append('[');
                for (int i = 0; i < items.Count; i++)
                {
                    if (i > 0) sb.Append(',');
                    Indent(sb, pretty, level + 1);
                    WriteValue(sb, items[i], pretty, level + 1);
                }
                Indent(sb, pretty, level);
                sb.Append(']');
                return;
            }
            WriteString(sb, v.ToString());
        }

        static void WriteString(StringBuilder sb, string s)
        {
            sb.Append('"');
            foreach (char c in s)
            {
                switch (c)
                {
                    case '"': sb.Append("\\\""); break;
                    case '\\': sb.Append("\\\\"); break;
                    case '\n': sb.Append("\\n"); break;
                    case '\r': sb.Append("\\r"); break;
                    case '\t': sb.Append("\\t"); break;
                    default:
                        if (c < 0x20) sb.Append("\\u").Append(((int)c).ToString("x4"));
                        else sb.Append(c);
                        break;
                }
            }
            sb.Append('"');
        }
    }

    // ------------------------------------------------------------------ logging

    class StderrLogger : ILogger
    {
        public void Log(string t, params object[] v) { Console.Error.WriteLine("log: " + Fmt(t, v)); }
        public void LogWarning(string t, params object[] v) { Console.Error.WriteLine("warn: " + Fmt(t, v)); }
        public void LogError(string t, params object[] v) { Console.Error.WriteLine("error: " + Fmt(t, v)); }
        static string Fmt(string t, object[] v)
        {
            try { return v == null || v.Length == 0 ? t : string.Format(t, v); } catch { return t; }
        }
    }

    class HarnessException : Exception
    {
        public HarnessException(string msg) : base(msg) { }
    }

    // ------------------------------------------------------------------ entry point

    static class Program
    {
        internal static string EditorDir;
        internal static string GameDir;
        static Assembly sdkAsm;
        static bool loadingSdk;
        static readonly Dictionary<string, Assembly> resolved = new Dictionary<string, Assembly>(StringComparer.OrdinalIgnoreCase);

        [System.Runtime.InteropServices.DllImport("kernel32.dll")]
        static extern IntPtr GetStdHandle(int nStdHandle);
        [System.Runtime.InteropServices.DllImport("kernel32.dll")]
        static extern bool SetStdHandle(int nStdHandle, IntPtr handle);

        [STAThread]
        static int Main(string[] args)
        {
            EditorDir = Environment.GetEnvironmentVariable("MU_MMC_EDITOR_DIR");
            if (string.IsNullOrEmpty(EditorDir))
                EditorDir = @"C:\Madden27Modding\MMC_Modding_Tools_v1.1.0.7\MMC_Editor_v1.1.0.7";
            GameDir = Environment.GetEnvironmentVariable("MU_MADDEN_GAME_DIR");
            if (string.IsNullOrEmpty(GameDir))
                GameDir = @"C:\Program Files\EA Games\Madden NFL 27";

            // stdout carries exactly one JSON object: route everything else to stderr. Keep a stream
            // on the real stdout, then point the process's Win32 stdout handle at stderr too, so
            // native DLLs loaded later (dxtex.dll prints "[Using DirectCompute ...]") write there.
            var realOut = new StreamWriter(Console.OpenStandardOutput(), new UTF8Encoding(false));
            realOut.AutoFlush = true;
            Console.SetOut(Console.Error);
            try { SetStdHandle(-11, GetStdHandle(-12)); } catch { }

            AppDomain.CurrentDomain.AssemblyResolve += Resolve;

            JObj result;
            try
            {
                if (!Directory.Exists(EditorDir))
                    throw new HarnessException("MMC Editor folder not found: " + EditorDir + " (set MU_MMC_EDITOR_DIR)");
                result = Commands.Dispatch(args);
                if (result.All(kv => kv.Key != "ok"))
                    result.Insert(0, new KeyValuePair<string, object>("ok", true));
            }
            catch (Exception ex)
            {
                var inner = ex;
                while (inner is TargetInvocationException && inner.InnerException != null) inner = inner.InnerException;
                Console.Error.WriteLine(inner.ToString());
                result = new JObj().Set("ok", false).Set("error", inner is HarnessException ? inner.Message : inner.GetType().Name + ": " + inner.Message);
            }
            bool ok = result.Any(kv => kv.Key == "ok" && kv.Value is bool && (bool)kv.Value);
            realOut.WriteLine(Json.Write(result, false));
            realOut.Flush();
            // Some SDK/WPF threads are foreground; do not wait for them.
            Environment.Exit(ok ? 0 : 1);
            return ok ? 0 : 1;
        }

        static Assembly Resolve(object sender, ResolveEventArgs e)
        {
            var name = new AssemblyName(e.Name).Name;
            if (name.EndsWith(".resources", StringComparison.OrdinalIgnoreCase)) return null;
            if (name == "EbxClasses")
            {
                // The profile SDK. Guard re-entry: LoadFile can trigger another resolve of the same name.
                if (sdkAsm == null && !loadingSdk)
                {
                    loadingSdk = true;
                    try { sdkAsm = Assembly.LoadFile(Path.Combine(EditorDir, "Profiles", "MADDEN27SDK.dll")); }
                    finally { loadingSdk = false; }
                }
                return sdkAsm;
            }
            Assembly cached;
            if (resolved.TryGetValue(name, out cached)) return cached;
            foreach (var la in AppDomain.CurrentDomain.GetAssemblies())
                if (la.GetName().Name == name) { resolved[name] = la; return la; }
            foreach (var d in new[] { EditorDir, Path.Combine(EditorDir, "Plugins"), Path.Combine(EditorDir, "ThirdParty"), Path.Combine(EditorDir, "Profiles") })
            {
                var p = Path.Combine(d, name + ".dll");
                if (File.Exists(p)) { var a = Assembly.LoadFrom(p); resolved[name] = a; return a; }
            }
            return null;
        }
    }

    // ------------------------------------------------------------------ start-up

    static class Boot
    {
        internal static FileSystemManager Fs;
        internal static ResourceManager Rm;
        internal static AssetManager Am;
        internal static ILogger Log = new StderrLogger();
        internal static string ProfileKey;
        internal static string KeyFile;
        static bool booted;

        internal static bool InitfsLoaded { get { return Fs != null && Fs.EnumerateFilesInMemoryFs().Any(); } }

        /// <summary>Ebx reads need the shared type descriptors from the (encrypted) initfs.</summary>
        internal static void RequireInitfs()
        {
            if (InitfsLoaded) return;
            throw new HarnessException("the game's initfs isn't loaded, so ebx can't be read: MMC Editor keeps the game key it asked for on first start in "
                + (KeyFile ?? "<editor>\\madden27.key") + ". Put that file there (or set MU_MMC_KEY_FILE to it).");
        }
        static System.Windows.Application wpfApp;

        /// <summary>The start-up sequence proven by spike S-c (MOD_BUILDER_PLAN §3).</summary>
        [MethodImpl(MethodImplOptions.NoInlining)]
        internal static void Start()
        {
            if (booted) return;
            var sw = Stopwatch.StartNew();

            // A WPF Application with a hidden main window: some editor code reads Application.Current.
            wpfApp = new System.Windows.Application();
            wpfApp.ShutdownMode = System.Windows.ShutdownMode.OnExplicitShutdown;
            var hidden = new System.Windows.Window();
            hidden.Width = 1; hidden.Height = 1; hidden.Left = -10000;
            hidden.ShowInTaskbar = false; hidden.WindowStyle = System.Windows.WindowStyle.None;
            wpfApp.MainWindow = hidden;

            // 1. The editor folder is the working directory (dxtex.dll, Profiles, Caches are relative).
            Environment.CurrentDirectory = Program.EditorDir;

            var cfg = Path.Combine(Path.GetTempPath(), "mu_mmc_harness_config.json");
            if (!File.Exists(cfg)) File.WriteAllText(cfg, "{}");
            Frosty.Core.Config.Load(cfg);

            // 2. Load the embedded profile list, then the Madden27 profile.
            var initList = typeof(ProfilesLibrary).GetMethods().First(m => m.Name == "Initialize" && m.GetParameters().Length == 1 && m.GetParameters()[0].ParameterType != typeof(string));
            var elem = initList.GetParameters()[0].ParameterType.GetGenericArguments()[0];
            initList.Invoke(null, new object[] { Array.CreateInstance(elem, 0) });
            var profs = (IEnumerable)typeof(ProfilesLibrary).GetField("m_profiles", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);
            var names = new List<string>();
            foreach (var pr in profs) names.Add(Member(pr, "Name").ToString());
            ProfileKey = names.FirstOrDefault(k => k.Equals("Madden27", StringComparison.OrdinalIgnoreCase))
                ?? names.FirstOrDefault(k => k.ToLowerInvariant().Contains("madden27"));
            if (ProfileKey == null) throw new HarnessException("no Madden27 profile in FrostySdk (profiles: " + string.Join(", ", names) + ")");
            if (!ProfilesLibrary.Initialize(ProfileKey)) throw new HarnessException("ProfilesLibrary.Initialize(" + ProfileKey + ") failed");

            // 3. EbxClasses resolves to Profiles\MADDEN27SDK.dll (Program.Resolve).
            TypeLibrary.Initialize(true);

            // 4. File system.
            if (!Directory.Exists(Program.GameDir)) throw new HarnessException("game folder not found: " + Program.GameDir + " (set MU_MADDEN_GAME_DIR)");
            Fs = new FileSystemManager(Program.GameDir);
            foreach (var src in ProfilesLibrary.Sources)
                Fs.AddSource((string)Member(src, "Path"), (bool)Member(src, "SubDirs"));
            // The game's initfs is encrypted with the game key the editor asks for on first start and
            // keeps in <editor>\<cache>.key (SplashWindow). Without it the initfs isn't read, so
            // res/chunk assets (textures) work but ebx can't (no shared type descriptors).
            byte[] key = null;
            if (ProfilesLibrary.RequiresKey)
            {
                KeyFile = Environment.GetEnvironmentVariable("MU_MMC_KEY_FILE");
                if (string.IsNullOrEmpty(KeyFile)) KeyFile = Path.Combine(Program.EditorDir, ProfilesLibrary.CacheName + ".key");
                if (File.Exists(KeyFile))
                {
                    var data = File.ReadAllBytes(KeyFile);
                    if (data.Length >= 16)
                    {
                        key = new byte[16]; Array.Copy(data, key, 16);
                        KeyManager.Instance.AddKey("Key1", key);
                        if (data.Length >= 32) { var k2 = new byte[16]; Array.Copy(data, 16, k2, 0, 16); KeyManager.Instance.AddKey("Key2", k2); }
                        if (data.Length >= 32 + 16384) { var k3 = new byte[16384]; Array.Copy(data, 32, k3, 0, 16384); KeyManager.Instance.AddKey("Key3", k3); }
                    }
                }
                else Console.Error.WriteLine("harness: no game key file at " + KeyFile + "; the initfs is not loaded (ebx commands will fail)");
            }
            Fs.Initialize(key);
            Console.Error.WriteLine("harness: file system head=" + Fs.Head + " catalogs=" + Fs.CatalogCount + " " + sw.Elapsed);

            // 5. Resource and asset managers (uses the editor's existing cache).
            Rm = new ResourceManager(Fs); Rm.SetLogger(Log); Rm.Initialize();
            Am = new AssetManager(Fs, Rm); Am.SetLogger(Log);

            // 6. The Frosty.Core.App globals the editor code reads.
            Frosty.Core.App.FileSystemManager = Fs;
            Frosty.Core.App.ResourceManager = Rm;
            Frosty.Core.App.AssetManager = Am;
            Frosty.Core.App.Logger = Log;
            Frosty.Core.App.IsEditor = true;
            Frosty.Core.App.SelectedProfile = ProfileKey;
            Frosty.Core.App.NotificationManager = new Frosty.Core.Managers.NotificationManager();
            Frosty.Core.App.PluginManager = new Frosty.Core.PluginManager(Log, Frosty.Core.PluginManagerType.Editor);
            try { Frosty.Core.App.PluginManager.Initialize(); }
            catch (Exception ex) { Console.Error.WriteLine("harness: PluginManager.Initialize failed (continuing): " + ex.Message); }

            Am.Initialize(true, new AssetManagerImportResult());
            Console.Error.WriteLine("harness: asset manager ebx=" + Am.GetEbxCount() + " " + sw.Elapsed);
            booted = true;
        }

        internal static object Member(object o, string name)
        {
            var t = o.GetType();
            var f = t.GetField(name);
            if (f != null) return f.GetValue(o);
            var p = t.GetProperty(name);
            if (p != null) return p.GetValue(o, null);
            throw new HarnessException("no member " + t.Name + "." + name);
        }
    }

    // ------------------------------------------------------------------ projects

    static class Projects
    {
        /// <summary>
        /// Loads a .fbproject without a window. FrostyProject.Load runs its body inside
        /// FrostyTaskWindow.Show (a WPF dialog that cannot be built headless), so this repeats
        /// Load/InternalLoad's header checks and then calls the private LoadTaskBody with a
        /// never-shown FrostyTaskWindow (its Update only sets two fields).
        /// </summary>
        internal static Frosty.Core.FrostyProject Open(string path)
        {
            var project = new Frosty.Core.FrostyProject();
            if (!File.Exists(path)) return project;
            var t = typeof(Frosty.Core.FrostyProject);
            Field(t, "filename").SetValue(project, path);
            var mig = t.GetField("requiresSchemaMigrationBackup", AnyField);
            if (mig != null) mig.SetValue(project, false);

            ulong magic = Convert.ToUInt64(Field(t, "Magic").GetValue(null));
            using (var reader = new NativeReader(new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read)))
            {
                if (reader.ReadULong() != magic)
                    throw new HarnessException("not a current-format .fbproject (legacy projects are not supported headless): " + path);
                uint version = reader.ReadUInt();
                Field(t, "projectVersion").SetValue(project, version);
                uint current = Convert.ToUInt32(Field(t, "FormatVersion").GetValue(null));
                if (version != current)
                    throw new HarnessException("project format version " + version + " != editor's " + current + "; open and re-save it in MMC Editor first");
                string profile = reader.ReadNullTerminatedString();
                Field(t, "gameProfile").SetValue(project, profile);
                if (!string.Equals(profile, ProfilesLibrary.ProfileName, StringComparison.OrdinalIgnoreCase))
                    throw new HarnessException("project is for profile " + profile + ", not " + ProfilesLibrary.ProfileName);

                var task = (Frosty.Core.Windows.FrostyTaskWindow)FormatterServices.GetUninitializedObject(typeof(Frosty.Core.Windows.FrostyTaskWindow));
                var body = t.GetMethod("LoadTaskBody", BindingFlags.NonPublic | BindingFlags.Instance);
                if (body == null) throw new HarnessException("FrostyProject.LoadTaskBody not found (editor version changed?)");
                body.Invoke(project, new object[] { reader, task });
            }
            Console.Error.WriteLine("harness: loaded project " + path + " (modified assets: " + Boot.Am.GetModifiedCount() + ")");
            return project;
        }

        const BindingFlags AnyField = BindingFlags.NonPublic | BindingFlags.Public | BindingFlags.Instance | BindingFlags.Static;

        static FieldInfo Field(Type t, string name)
        {
            var f = t.GetField(name, AnyField);
            if (f == null) throw new HarnessException("FrostyProject." + name + " not found (editor version changed?)");
            return f;
        }

        internal static void Save(Frosty.Core.FrostyProject project, string path)
        {
            var dir = Path.GetDirectoryName(Path.GetFullPath(path));
            if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
            project.Save(path, true);
        }
    }

    // ------------------------------------------------------------------ commands

    static class Commands
    {
        const string Usage =
            "usage: MmcHarness <command> ...\n" +
            "  info\n" +
            "  find <pattern> [--kind ebx|res|chunk] [--limit N]\n" +
            "  export-texture <asset> <out.png|out.dds|out.tga> [--project P]\n" +
            "  import-texture <project.fbproject> <asset> <in.png|in.dds|in.tga> [--raw]\n" +
            "  ebx-dump <asset> <out.json> [--project P]\n" +
            "  ebx-set <project.fbproject> <asset> <field.path[0].x> <value>\n" +
            "  export-mod <project.fbproject> <out.fbmod> --title T --author A --version V [--category C] [--description D]";

        internal static JObj Dispatch(string[] args)
        {
            if (args.Length == 0 || args[0] == "-h" || args[0] == "--help" || args[0] == "help")
                throw new HarnessException(Usage);
            var opts = new Dictionary<string, string>();
            var flags = new HashSet<string>();
            var pos = new List<string>();
            for (int i = 1; i < args.Length; i++)
            {
                var a = args[i];
                if (a == "--raw") { flags.Add(a); continue; }
                if (a.StartsWith("--") && a.Length > 2)
                {
                    if (i + 1 >= args.Length) throw new HarnessException("missing value for " + a);
                    opts[a.Substring(2)] = args[++i];
                    continue;
                }
                pos.Add(a);
            }
            switch (args[0])
            {
                case "info": Need(pos, 0, "info"); return Info();
                case "find": Need(pos, 1, "find <pattern>"); return Find(pos[0], Opt(opts, "kind", null), int.Parse(Opt(opts, "limit", "200")));
                case "export-texture": Need(pos, 2, "export-texture <asset> <out>"); return ExportTexture(pos[0], pos[1], Opt(opts, "project", null));
                case "import-texture": Need(pos, 3, "import-texture <project> <asset> <in>"); return ImportTexture(pos[0], pos[1], pos[2], flags.Contains("--raw"));
                case "ebx-dump": Need(pos, 2, "ebx-dump <asset> <out.json>"); return EbxDump(pos[0], pos[1], Opt(opts, "project", null));
                case "ebx-set": Need(pos, 4, "ebx-set <project> <asset> <path> <value>"); return EbxSet(pos[0], pos[1], pos[2], pos[3]);
                case "export-mod":
                    Need(pos, 2, "export-mod <project> <out.fbmod> --title T --author A --version V");
                    foreach (var k in new[] { "title", "author", "version" })
                        if (!opts.ContainsKey(k)) throw new HarnessException("export-mod needs --" + k);
                    return ExportMod(pos[0], pos[1], opts);
                default: throw new HarnessException("unknown command '" + args[0] + "'\n" + Usage);
            }
        }

        static void Need(List<string> pos, int n, string usage)
        {
            if (pos.Count != n) throw new HarnessException("expected " + n + " argument(s): " + usage);
        }

        static string Opt(Dictionary<string, string> o, string k, string def)
        {
            string v; return o.TryGetValue(k, out v) ? v : def;
        }

        static string Norm(string assetName)
        {
            return assetName.Replace('\\', '/').Trim().TrimStart('/');
        }

        static EbxAssetEntry Ebx(string name)
        {
            var e = Boot.Am.GetEbxEntry(Norm(name));
            if (e == null) throw new HarnessException("no ebx asset named " + name);
            return e;
        }

        // ---- info
        static JObj Info()
        {
            Boot.Start();
            var r = new JObj().Set("ok", true);
            var exe = Path.Combine(Program.EditorDir, "MMCEditor.exe");
            r.Set("editor_dir", Program.EditorDir);
            r.Set("editor_version", File.Exists(exe) ? FileVersionInfo.GetVersionInfo(exe).FileVersion : null);
            r.Set("profile", ProfilesLibrary.ProfileName);
            r.Set("profile_display_name", ProfilesLibrary.DisplayName);
            r.Set("data_version", ProfilesLibrary.DataVersion);
            r.Set("game_dir", Program.GameDir);
            r.Set("game_build", Boot.Fs.Head);
            r.Set("game_base", Boot.Fs.Base);
            r.Set("catalogs", Boot.Fs.CatalogCount);
            r.Set("ebx_count", Boot.Am.GetEbxCount());
            r.Set("must_add_chunks", ProfilesLibrary.MustAddChunks);
            r.Set("requires_key", ProfilesLibrary.RequiresKey);
            r.Set("key_file", Boot.KeyFile);
            r.Set("key_file_present", Boot.KeyFile != null && File.Exists(Boot.KeyFile));
            r.Set("initfs_files", Boot.Fs.EnumerateFilesInMemoryFs().Count());
            r.Set("ebx_readable", Boot.InitfsLoaded);
            // The 20-byte game-layout fingerprint (MOD_REVERSE_ENGINEERING §2.1) follows the build
            // number in the editor's cache header: magic(8) version(4) ?(4) build(4) fingerprint(20).
            var cache = Path.Combine(Program.EditorDir, "Caches", ProfilesLibrary.CacheName + ".cache");
            if (File.Exists(cache))
            {
                var head = new byte[40];
                using (var fsr = new FileStream(cache, FileMode.Open, FileAccess.Read, FileShare.ReadWrite)) fsr.Read(head, 0, 40);
                r.Set("cache_build", BitConverter.ToUInt32(head, 16));
                r.Set("fingerprint", BitConverter.ToString(head, 20, 20).Replace("-", "").ToLowerInvariant());
            }
            r.Set("harness_version", "1.0");
            return r;
        }

        // ---- find
        static JObj Find(string pattern, string kind, int limit)
        {
            Boot.Start();
            if (kind != null && kind != "ebx" && kind != "res" && kind != "chunk")
                throw new HarnessException("--kind must be ebx, res or chunk");
            Func<string, bool> match = MakeMatcher(pattern);
            var hits = new List<object>();
            int total = 0;
            if (kind == null || kind == "ebx")
                foreach (var e in Boot.Am.EnumerateEbx("", false, false, true, ""))
                    if (match(e.Name)) { total++; if (hits.Count < limit) hits.Add(new JObj().Set("name", e.Name).Set("kind", "ebx").Set("type", e.Type)); }
            if (kind == null || kind == "res")
                foreach (var e in Boot.Am.EnumerateRes(0, false, ""))
                    if (match(e.Name)) { total++; if (hits.Count < limit) hits.Add(new JObj().Set("name", e.Name).Set("kind", "res").Set("type", e.Type).Set("res_rid", e.ResRid.ToString("x16"))); }
            if (kind == null || kind == "chunk")
                foreach (var e in Boot.Am.EnumerateChunks(false))
                    if (match(e.Id.ToString())) { total++; if (hits.Count < limit) hits.Add(new JObj().Set("name", e.Id.ToString()).Set("kind", "chunk").Set("type", null)); }
            return new JObj().Set("ok", true).Set("pattern", pattern).Set("count", total).Set("truncated", total > hits.Count).Set("matches", hits);
        }

        static Func<string, bool> MakeMatcher(string pattern)
        {
            var p = Norm(pattern).ToLowerInvariant();
            if (p.IndexOfAny(new[] { '*', '?' }) < 0) return s => s != null && s.ToLowerInvariant().Contains(p);
            var rx = new System.Text.RegularExpressions.Regex("^" + System.Text.RegularExpressions.Regex.Escape(p).Replace("\\*", ".*").Replace("\\?", ".") + "$");
            return s => s != null && rx.IsMatch(s.ToLowerInvariant());
        }

        // ---- textures
        static Texture LoadTexture(EbxAssetEntry ebx, out ResAssetEntry res)
        {
            res = Boot.Am.GetResEntry(ebx.Name);
            if (res == null)
            {
                // Fall back to the TextureAsset's Resource field (a ResourceRef, implicitly a ulong).
                var root = Boot.Am.GetEbx(ebx, false).RootObject;
                var prop = root.GetType().GetProperty("Resource");
                if (prop != null)
                {
                    var rr = prop.GetValue(root, null);
                    var conv = rr.GetType().GetMethods(BindingFlags.Public | BindingFlags.Static).FirstOrDefault(m => m.Name == "op_Implicit" && m.ReturnType == typeof(ulong));
                    if (conv != null) res = Boot.Am.GetResEntry((ulong)conv.Invoke(null, new[] { rr }));
                }
            }
            if (res == null) throw new HarnessException(ebx.Name + " has no texture resource (is it a TextureAsset?)");
            return Boot.Am.GetResAs<Texture>(res, null);
        }

        static JObj TexInfo(Texture t)
        {
            return new JObj().Set("width", t.Width).Set("height", t.Height).Set("depth", t.Depth)
                .Set("mips", t.MipCount).Set("first_mip", t.FirstMip).Set("format", t.PixelFormat)
                .Set("type", t.Type.ToString()).Set("flags", (int)t.Flags).Set("chunk", t.ChunkId.ToString());
        }

        static void OpenOptionalProject(string projectPath)
        {
            if (projectPath == null) return;
            if (!File.Exists(projectPath)) throw new HarnessException("project not found: " + projectPath);
            Projects.Open(projectPath);
        }

        static JObj ExportTexture(string asset, string outPath, string projectPath)
        {
            Boot.Start();
            OpenOptionalProject(projectPath);
            var ebx = Ebx(asset);
            ResAssetEntry res;
            var tex = LoadTexture(ebx, out res);
            var ext = Path.GetExtension(outPath).ToLowerInvariant();
            if (ext != ".png" && ext != ".dds" && ext != ".tga" && ext != ".hdr")
                throw new HarnessException("export-texture writes .png, .dds, .tga or .hdr, not " + ext);
            var dir = Path.GetDirectoryName(Path.GetFullPath(outPath));
            if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
            new TexturePlugin.TextureExporter().Export(tex, outPath, "*" + ext);
            if (!File.Exists(outPath)) throw new HarnessException("TextureExporter wrote nothing to " + outPath);
            return new JObj().Set("ok", true).Set("asset", ebx.Name).Set("out", outPath)
                .Set("bytes", new FileInfo(outPath).Length).Set("texture", TexInfo(tex));
        }

        static JObj ImportTexture(string projectPath, string asset, string inPath, bool raw)
        {
            Boot.Start();
            if (!File.Exists(inPath)) throw new HarnessException("input not found: " + inPath);
            var project = Projects.Open(projectPath);
            var ebx = Ebx(asset);
            ResAssetEntry res;
            var orig = LoadTexture(ebx, out res);
            var before = TexInfo(orig);
            var note = TextureImport.Import(orig, ebx, res, inPath, raw);
            Projects.Save(project, projectPath);
            var newTex = Boot.Am.GetResAs<Texture>(res, null);
            return new JObj().Set("ok", true).Set("project", projectPath).Set("asset", ebx.Name)
                .Set("input", inPath).Set("original", before).Set("imported", TexInfo(newTex)).Set("note", note)
                .Set("modified_assets", Boot.Am.GetModifiedCount());
        }

        // ---- ebx
        static JObj EbxDump(string asset, string outPath, string projectPath)
        {
            Boot.Start();
            Boot.RequireInitfs();
            OpenOptionalProject(projectPath);
            var entry = Ebx(asset);
            var ebx = Boot.Am.GetEbx(entry, false);
            var d = new EbxDumper();
            var root = d.Dump(ebx.RootObject, "");
            var doc = new JObj().Set("asset", entry.Name).Set("type", entry.Type)
                .Set("file_guid", ebx.FileGuid.ToString()).Set("object_count", ebx.Objects.Count())
                .Set("color_fields", d.ColorFields).Set("root", root);
            var dir = Path.GetDirectoryName(Path.GetFullPath(outPath));
            if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
            File.WriteAllText(outPath, Json.Write(doc, true), new UTF8Encoding(false));
            return new JObj().Set("ok", true).Set("asset", entry.Name).Set("type", entry.Type).Set("out", outPath)
                .Set("fields", d.FieldCount).Set("color_fields", d.ColorFields.Take(50).ToList())
                .Set("color_field_count", d.ColorFields.Count);
        }

        static JObj EbxSet(string projectPath, string asset, string path, string value)
        {
            Boot.Start();
            Boot.RequireInitfs();
            var project = Projects.Open(projectPath);
            var entry = Ebx(asset);
            var ebx = Boot.Am.GetEbx(entry, false);
            object oldVal, newVal; Type fieldType;
            EbxPath.Set(ebx.RootObject, path, value, out oldVal, out newVal, out fieldType);
            Boot.Am.ModifyEbx(entry.Name, ebx);
            Projects.Save(project, projectPath);
            var d = new EbxDumper();
            return new JObj().Set("ok", true).Set("project", projectPath).Set("asset", entry.Name).Set("path", path)
                .Set("field_type", fieldType.Name).Set("old", d.Dump(oldVal, path)).Set("new", d.Dump(newVal, path))
                .Set("modified_assets", Boot.Am.GetModifiedCount());
        }

        // ---- export
        static JObj ExportMod(string projectPath, string outPath, Dictionary<string, string> opts)
        {
            Boot.Start();
            if (!File.Exists(projectPath)) throw new HarnessException("project not found: " + projectPath);
            var project = Projects.Open(projectPath);
            if (Boot.Am.GetModifiedCount() == 0) throw new HarnessException("project has no modified assets: " + projectPath);
            var ms = project.GetModSettings();
            var set = new[] {
                new[] { "Title", Opt(opts, "title", null) }, new[] { "Author", Opt(opts, "author", null) },
                new[] { "Version", Opt(opts, "version", null) }, new[] { "Category", Opt(opts, "category", "") },
                new[] { "Description", Opt(opts, "description", "") } };
            foreach (var kv in set)
            {
                var pi = ms.GetType().GetProperty(kv[0]);
                if (pi == null) throw new HarnessException("ModSettings has no " + kv[0]);
                pi.SetValue(ms, kv[1], null);
            }
            var dir = Path.GetDirectoryName(Path.GetFullPath(outPath));
            if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
            project.WriteToMod(outPath, ms);
            if (!File.Exists(outPath)) throw new HarnessException("WriteToMod wrote nothing to " + outPath);
            return new JObj().Set("ok", true).Set("project", projectPath).Set("out", outPath)
                .Set("bytes", new FileInfo(outPath).Length).Set("title", ms.Title).Set("author", ms.Author)
                .Set("version", ms.Version).Set("category", ms.Category).Set("modified_assets", Boot.Am.GetModifiedCount());
        }
    }

    // ------------------------------------------------------------------ texture import

    /// <summary>
    /// The body of TexturePlugin.TextureImporter's task callback, without the FrostyTaskWindow.
    /// Mirrors MMC's TextureImporter (read from its IL) and Frosty Toolsuite's import path:
    /// make a DDS in the texture's own pixel format, read its header, build a new Texture,
    /// CalculateMipData, ModifyChunk (or AddChunk when the profile must add chunks), SetData,
    /// AssetNameHash, ModifyRes, LinkAsset.
    /// </summary>
    static class TextureImport
    {
        class Dds
        {
            public int Width, Height, Depth, Mips;
            public string Format;  // Frosty pixel format name, or null if unknown
            public string Label;   // human-readable source format
            public bool Srgb;
            public int HeaderSize;
        }

        internal static string Import(Texture orig, EbxAssetEntry ebx, ResAssetEntry res, string inPath, bool raw)
        {
            if (orig.Type != TextureType.TT_2d)
                throw new HarnessException("only 2D textures are supported headless (this one is " + orig.Type + ")");
            byte[] input = File.ReadAllBytes(inPath);
            string note;
            byte[] dds;
            var ext = Path.GetExtension(inPath).ToLowerInvariant();
            if (ext == ".dds")
            {
                var h = ReadHeader(input);
                if (h.Format == orig.PixelFormat || (raw && h.Format != null))
                {
                    dds = input;
                    note = "DDS imported as is (" + h.Format + ")";
                }
                else if (raw)
                {
                    throw new HarnessException("--raw: DDS pixel format is not one Frosty knows");
                }
                else
                {
                    // Re-encode into the texture's own format: DDS -> PNG -> DDS(orig format), both
                    // through the editor's native dxtex.dll (no window involved).
                    var png = Convert(input, TexturePlugin.ImageFormat.DDS, null);
                    dds = Convert(png, TexturePlugin.ImageFormat.PNG, orig);
                    note = "DDS " + h.Label + " re-encoded to " + orig.PixelFormat + " via dxtex";
                }
            }
            else
            {
                TexturePlugin.ImageFormat fmt;
                if (!TexturePlugin.FrostyTextureEditor.TryGetImageFormat(inPath, out fmt))
                    throw new HarnessException("unsupported input type " + ext + " (use .png, .tga, .hdr or .dds)");
                dds = Convert(input, fmt, orig);
                note = ext.TrimStart('.').ToUpperInvariant() + " encoded to " + orig.PixelFormat + " via dxtex";
            }

            var hdr = ReadHeader(dds);
            string pixelFormat;
            TextureFlags flags;
            if (!ReferenceEquals(dds, input))
            {
                // Encoded by dxtex into the texture's own format (TextureImporter does the same).
                pixelFormat = orig.PixelFormat;
                flags = (TextureFlags)((int)orig.Flags & 2);
            }
            else
            {
                pixelFormat = hdr.Format;
                flags = hdr.Srgb ? (TextureFlags)2 : (TextureFlags)0;
                bool origSrgb = orig.PixelFormat.Contains("SRGB") || ((int)orig.Flags & 2) != 0;
                if (origSrgb && !hdr.Srgb) throw new HarnessException("this texture is SRGB: the DDS must use an SRGB format");
            }
            if (pixelFormat == null) throw new HarnessException("unsupported DDS pixel format in " + inPath);
            if (hdr.Width <= 0 || hdr.Height <= 0 || hdr.Width > 16384 || hdr.Height > 16384)
                throw new HarnessException("texture dimensions " + hdr.Width + "x" + hdr.Height + " are invalid for Direct3D 11");
            int mipCount = Math.Max(1, hdr.Mips);
            if (mipCount > 1 + (int)Math.Floor(Math.Log(Math.Max(hdr.Width, hdr.Height), 2)) || mipCount > 15)
                throw new HarnessException("invalid mip count " + mipCount + " for " + hdr.Width + "x" + hdr.Height);
            if (Frosty.Core.Viewport.TextureUtils.IsCompressedFormat(pixelFormat) && orig.MipCount > 1 && (hdr.Width % 4 != 0 || hdr.Height % 4 != 0))
                throw new HarnessException("width/height must be divisible by 4 for compressed formats with mips");

            var am = Boot.Am;
            var chunkEntry = am.GetChunkEntry(orig.ChunkId);
            byte[] data = new byte[dds.Length - hdr.HeaderSize];
            Buffer.BlockCopy(dds, hdr.HeaderSize, data, 0, data.Length);

            var newTex = new Texture(orig.Type, pixelFormat, (ushort)hdr.Width, (ushort)hdr.Height, 1, orig.Version, orig.ResourceMeta);
            newTex.FirstMip = orig.FirstMip;
            if (mipCount <= orig.FirstMip) newTex.FirstMip = 0;
            newTex.TextureGroup = orig.TextureGroup;
            newTex.CalculateMipData((byte)mipCount, Frosty.Core.Viewport.TextureUtils.GetFormatBlockSize(pixelFormat),
                Frosty.Core.Viewport.TextureUtils.IsCompressedFormat(pixelFormat), (uint)data.Length);
            newTex.Flags = flags;
            newTex.Flags = newTex.Flags | (TextureFlags)((int)orig.Flags & 0xFD);

            bool streamed = ((int)newTex.Flags & 8) != 0;
            if (ProfilesLibrary.MustAddChunks && chunkEntry != null && chunkEntry.Bundles.Count == 0 && !chunkEntry.IsAdded)
            {
                var superBundles = chunkEntry.SuperBundles;
                orig.ChunkId = am.AddChunk(data, null, streamed ? null : newTex, new int[0]);
                chunkEntry = am.GetChunkEntry(orig.ChunkId);
                chunkEntry.AddedSuperBundles.AddRange(superBundles);
            }
            else
            {
                am.ModifyChunk(orig.ChunkId, data, (!streamed && newTex.Type == TextureType.TT_2d) ? newTex : null);
            }
            for (int i = 0; i < 4; i++) newTex.Unknown3[i] = orig.Unknown3[i];
            newTex.SetData(orig.ChunkId, am);
            if (ProfilesLibrary.IsLoaded(new[] { (ProfileVersion)20260605, (ProfileVersion)20260612 }))
                newTex.AssetNameHash = unchecked((ulong)Utils.HashString64(res.Name, true));
            else
                newTex.AssetNameHash = (ulong)(uint)Frosty.Hash.Fnv1.HashString(res.Name);
            ulong resRid = orig.ResourceId;
            orig.Dispose();
            am.ModifyRes(resRid, newTex);
            res.LinkAsset(chunkEntry);
            ebx.LinkAsset(res);
            Console.Error.WriteLine("harness: imported " + ebx.Name + " " + pixelFormat + " " + hdr.Width + "x" + hdr.Height + " mips=" + mipCount);
            return note;
        }

        /// <summary>Image bytes -> DDS bytes in the texture's own format (dest != null), or DDS -> PNG (dest == null).</summary>
        static byte[] Convert(byte[] src, TexturePlugin.ImageFormat srcFormat, Texture dest)
        {
            var blob = new TexturePlugin.BlobData();
            try
            {
                if (dest == null)
                    TexturePlugin.FrostyTextureEditor.ConvertDDSToImage(src, src.LongLength, TexturePlugin.ImageFormat.PNG, ref blob);
                else
                {
                    var options = TexturePlugin.FrostyTextureEditor.ResolveImportOptions(dest, srcFormat);
                    TexturePlugin.FrostyTextureEditor.ConvertImageToDDS(src, src.LongLength, srcFormat, options, ref blob);
                }
                var outBytes = blob.Data;
                if (outBytes == null || outBytes.Length == 0) throw new HarnessException("dxtex conversion produced no data");
                return outBytes;
            }
            finally
            {
                try { TexturePlugin.FrostyTextureEditor.ReleaseBlob(blob); } catch { }
            }
        }

        static Dds ReadHeader(byte[] b)
        {
            if (b.Length < 128 || BitConverter.ToUInt32(b, 0) != 0x20534444) throw new HarnessException("not a DDS file");
            var h = new Dds();
            h.Height = BitConverter.ToInt32(b, 12);
            h.Width = BitConverter.ToInt32(b, 16);
            h.Depth = BitConverter.ToInt32(b, 24);
            h.Mips = BitConverter.ToInt32(b, 28);
            uint pfFlags = BitConverter.ToUInt32(b, 80);
            string fourCC = Encoding.ASCII.GetString(b, 84, 4);
            uint bits = BitConverter.ToUInt32(b, 88);
            uint rMask = BitConverter.ToUInt32(b, 92), gMask = BitConverter.ToUInt32(b, 96), bMask = BitConverter.ToUInt32(b, 100), aMask = BitConverter.ToUInt32(b, 104);
            h.HeaderSize = 128;
            if ((pfFlags & 0x4) != 0 && fourCC == "DX10")
            {
                if (b.Length < 148) throw new HarnessException("truncated DX10 DDS header");
                h.HeaderSize = 148;
                int dxgi = BitConverter.ToInt32(b, 128);
                switch (dxgi)
                {
                    case 28: h.Format = "R8G8B8A8_UNORM"; break;
                    case 29: h.Format = "R8G8B8A8_SRGB"; h.Srgb = true; break;
                    case 61: h.Format = "R8_UNORM"; break;
                    case 10: h.Format = "R16G16B16A16_FLOAT"; break;
                    case 71: h.Format = "BC1_UNORM"; break;
                    case 72: h.Format = "BC1_SRGB"; h.Srgb = true; break;
                    case 77: h.Format = "BC3_UNORM"; break;
                    case 78: h.Format = "BC3_SRGB"; h.Srgb = true; break;
                    case 80: h.Format = "BC4_UNORM"; break;
                    case 83: h.Format = "BC5_UNORM"; break;
                    case 95: h.Format = "BC6U_FLOAT"; break;
                    case 98: h.Format = "BC7_UNORM"; break;
                    case 99: h.Format = "BC7_SRGB"; h.Srgb = true; break;
                }
            }
            else if ((pfFlags & 0x4) != 0)
            {
                switch (fourCC)
                {
                    case "DXT1": h.Format = "BC1_UNORM"; break;
                    case "DXT5": h.Format = "BC3_UNORM"; break;
                    case "ATI1": case "BC4U": h.Format = "BC4_UNORM"; break;
                    case "ATI2": case "BC5U": h.Format = "BC5_UNORM"; break;
                }
            }
            else if (bits == 32 && rMask == 0x000000FF && gMask == 0x0000FF00 && bMask == 0x00FF0000 && aMask == 0xFF000000)
            {
                h.Format = "R8G8B8A8_UNORM";
            }
            else if (bits == 32 && rMask == 0x00FF0000 && gMask == 0x0000FF00 && bMask == 0x000000FF)
            {
                h.Label = "B8G8R8A8 (no Frosty format)";
            }
            if (h.Label == null) h.Label = h.Format ?? ("unknown (fourCC " + fourCC.TrimEnd('\0') + ", " + bits + " bpp)");
            return h;
        }
    }

    // ------------------------------------------------------------------ ebx reflection

    static class EbxReflect
    {
        internal static bool IsLeaf(Type t)
        {
            return t.IsPrimitive || t.IsEnum || t == typeof(string) || t == typeof(Guid) || t == typeof(decimal);
        }

        internal static IEnumerable<PropertyInfo> Fields(Type t)
        {
            return t.GetProperties(BindingFlags.Public | BindingFlags.Instance)
                .Where(p => p.CanRead && p.GetIndexParameters().Length == 0 && p.GetGetMethod() != null)
                .Where(p => p.DeclaringType != typeof(object));
        }

        internal static bool IsVecLike(Type t)
        {
            var names = Fields(t).Where(p => p.PropertyType == typeof(float)).Select(p => p.Name.ToLowerInvariant()).ToList();
            return names.Contains("x") && names.Contains("y") && names.Contains("z") && names.Count <= 4;
        }

        internal static object Instance(Type t)
        {
            return t.IsValueType ? Activator.CreateInstance(t) : Activator.CreateInstance(t);
        }
    }

    class EbxDumper
    {
        readonly Dictionary<object, string> seen = new Dictionary<object, string>(new RefEq());
        public readonly List<object> ColorFields = new List<object>();
        public int FieldCount;
        const int MaxDepth = 64;

        class RefEq : IEqualityComparer<object>
        {
            public new bool Equals(object a, object b) { return ReferenceEquals(a, b); }
            public int GetHashCode(object o) { return RuntimeHelpers.GetHashCode(o); }
        }

        public object Dump(object v, string path) { return Dump(v, path, 0); }

        object Dump(object v, string path, int depth)
        {
            if (v == null) return null;
            var t = v.GetType();
            if (t.IsEnum) return v.ToString();
            if (t == typeof(Guid)) return v.ToString();
            if (EbxReflect.IsLeaf(t)) return v;
            if (depth > MaxDepth) return "<depth limit>";
            if (t.Name == "CString") return v.ToString();
            if (t.Name == "PointerRef") return DumpPointer(v, path, depth);
            if (t.Name == "ResourceRef" || t.Name == "FileRef" || t.Name == "TypeRef" || t.Name == "BoxedValueRef" || t.Name == "EbxImportReference")
                return new JObj().Set("$" + t.Name, v.ToString());
            var list = v as IList;
            if (list != null)
            {
                var arr = new List<object>(list.Count);
                for (int i = 0; i < list.Count; i++) arr.Add(Dump(list[i], path + "[" + i + "]", depth + 1));
                return arr;
            }
            if (!t.IsValueType)
            {
                string prev;
                if (seen.TryGetValue(v, out prev)) return new JObj().Set("$ref", prev);
                seen[v] = path;
            }
            var o = new JObj().Set("$type", t.Name);
            var gm = t.GetMethod("GetInstanceGuid", Type.EmptyTypes);
            if (gm != null) { try { o.Set("$guid", gm.Invoke(v, null).ToString()); } catch { } }
            if (EbxReflect.IsVecLike(t)) NoteColor(path, t, v);
            foreach (var p in EbxReflect.Fields(t))
            {
                object fv;
                try { fv = p.GetValue(v, null); } catch (Exception ex) { fv = "<error: " + ex.GetBaseException().Message + ">"; }
                var fp = path.Length == 0 ? p.Name : path + "." + p.Name;
                FieldCount++;
                var lname = p.Name.ToLowerInvariant();
                if ((lname.Contains("color") || lname.Contains("colour") || lname.Contains("tint")) && !EbxReflect.IsVecLike(p.PropertyType))
                    NoteColor(fp, p.PropertyType, fv);
                o.Set(p.Name, Dump(fv, fp, depth + 1));
            }
            return o;
        }

        void NoteColor(string path, Type t, object v)
        {
            if (ColorFields.Count >= 2000) return;
            object shown = null;
            if (v != null && EbxReflect.IsLeaf(v.GetType())) shown = v.GetType().IsEnum ? v.ToString() : v;
            else if (v != null && EbxReflect.IsVecLike(v.GetType()))
            {
                var vals = EbxReflect.Fields(v.GetType()).Where(p => p.PropertyType == typeof(float)).Select(p => (object)(float)p.GetValue(v, null)).ToList();
                shown = vals;
            }
            ColorFields.Add(new JObj().Set("path", path).Set("type", t.Name).Set("value", shown));
        }

        object DumpPointer(object pr, string path, int depth)
        {
            var type = Boot.Member(pr, "Type").ToString();
            if (type == "Null") return null;
            if (type == "Internal") return Dump(Boot.Member(pr, "Internal"), path, depth + 1);
            var ext = Boot.Member(pr, "External");
            var fileGuid = (Guid)Boot.Member(ext, "FileGuid");
            var classGuid = (Guid)Boot.Member(ext, "ClassGuid");
            string name = null;
            try { var e = Boot.Am.GetEbxEntry(fileGuid); if (e != null) name = e.Name; } catch { }
            return new JObj().Set("$external", name).Set("file_guid", fileGuid.ToString()).Set("class_guid", classGuid.ToString());
        }
    }

    /// <summary>Field paths: Name.Sub[2].x ; internal PointerRefs are followed transparently.</summary>
    static class EbxPath
    {
        class Seg { public string Name; public int Index = -1; }

        static List<Seg> Parse(string path)
        {
            var segs = new List<Seg>();
            foreach (var part in path.Split('.'))
            {
                if (part.Length == 0) throw new HarnessException("bad field path: " + path);
                int b = part.IndexOf('[');
                string name = b < 0 ? part : part.Substring(0, b);
                if (name.Length > 0) segs.Add(new Seg { Name = name });
                while (b >= 0)
                {
                    int e = part.IndexOf(']', b);
                    if (e < 0) throw new HarnessException("bad field path: " + path);
                    segs.Add(new Seg { Index = int.Parse(part.Substring(b + 1, e - b - 1), CultureInfo.InvariantCulture) });
                    b = part.IndexOf('[', e);
                }
            }
            if (segs.Count == 0) throw new HarnessException("empty field path");
            return segs;
        }

        static object Deref(object v)
        {
            if (v != null && v.GetType().Name == "PointerRef")
            {
                var type = Boot.Member(v, "Type").ToString();
                if (type == "Internal") return Boot.Member(v, "Internal");
                throw new HarnessException("path goes through a " + type + " pointer; only internal objects can be edited");
            }
            return v;
        }

        static PropertyInfo Prop(object cur, string name)
        {
            var p = EbxReflect.Fields(cur.GetType()).FirstOrDefault(x => x.Name == name)
                ?? EbxReflect.Fields(cur.GetType()).FirstOrDefault(x => string.Equals(x.Name, name, StringComparison.OrdinalIgnoreCase));
            if (p == null) throw new HarnessException(cur.GetType().Name + " has no field " + name);
            return p;
        }

        static object Get(object cur, Seg s, out Type t)
        {
            if (s.Index >= 0)
            {
                var list = cur as IList;
                if (list == null) throw new HarnessException("[" + s.Index + "] on a non-list " + cur.GetType().Name);
                if (s.Index >= list.Count) throw new HarnessException("index " + s.Index + " out of range (" + list.Count + ")");
                var gen = cur.GetType().IsGenericType ? cur.GetType().GetGenericArguments()[0] : typeof(object);
                t = gen;
                return list[s.Index];
            }
            var p = Prop(cur, s.Name);
            t = p.PropertyType;
            return p.GetValue(cur, null);
        }

        static void Put(object cur, Seg s, object v)
        {
            if (s.Index >= 0) { ((IList)cur)[s.Index] = v; return; }
            var p = Prop(cur, s.Name);
            if (!p.CanWrite) throw new HarnessException("field " + s.Name + " is read-only");
            p.SetValue(cur, v, null);
        }

        internal static void Set(object root, string path, string value, out object oldVal, out object newVal, out Type fieldType)
        {
            var segs = Parse(path);
            object o, n; Type ft;
            SetIn(root, segs, 0, value, out o, out n, out ft);
            oldVal = o; newVal = n; fieldType = ft;
        }

        // Returns the (possibly boxed value-type) object after the change, so callers can write structs back.
        static object SetIn(object cur, List<Seg> segs, int i, string value, out object oldVal, out object newVal, out Type fieldType)
        {
            cur = Deref(cur);
            if (cur == null) throw new HarnessException("null object on the path");
            Type t;
            var child = Get(cur, segs[i], out t);
            if (i == segs.Count - 1)
            {
                var deref = Deref(child);
                fieldType = deref != null ? deref.GetType() : t;
                oldVal = Snapshot(deref);
                var nv = Values.Convert(fieldType, deref, value);
                newVal = nv;
                if (child != null && child.GetType().Name == "PointerRef")
                {
                    if (!ReferenceEquals(nv, deref)) throw new HarnessException("cannot replace an object behind a pointer");
                }
                else Put(cur, segs[i], nv);
                return cur;
            }
            bool isPointer = child != null && child.GetType().Name == "PointerRef";
            var updated = SetIn(child, segs, i + 1, value, out oldVal, out newVal, out fieldType);
            if (!isPointer && updated != null && updated.GetType().IsValueType) Put(cur, segs[i], updated);
            return cur;
        }

        static object Snapshot(object v)
        {
            if (v == null || EbxReflect.IsLeaf(v.GetType()) || v.GetType().Name == "CString") return v;
            if (EbxReflect.IsVecLike(v.GetType()))
            {
                var o = new JObj();
                foreach (var p in EbxReflect.Fields(v.GetType())) o.Set(p.Name, p.GetValue(v, null));
                return o;
            }
            return v.ToString();
        }
    }

    static class Values
    {
        static ulong Bits(object o)
        {
            if (o is uint) return (uint)o;
            if (o is int) return unchecked((uint)(int)o);
            if (o is ulong) return (ulong)o;
            if (o is long) return unchecked((ulong)(long)o);
            return 0;
        }

        internal static object Convert(Type t, object old, string s)
        {
            var inv = CultureInfo.InvariantCulture;
            if (s.StartsWith("#"))
            {
                var hex = s.Substring(1);
                if ((hex.Length != 6 && hex.Length != 8) || !hex.All(Uri.IsHexDigit))
                    throw new HarnessException("colour must be #RRGGBB or #RRGGBBAA, not " + s);
                uint rgb = uint.Parse(hex.Substring(0, 6), NumberStyles.HexNumber, inv);
                int alpha = hex.Length == 8 ? int.Parse(hex.Substring(6, 2), NumberStyles.HexNumber, inv) : -1;
                float r = ((rgb >> 16) & 0xFF) / 255f, g = ((rgb >> 8) & 0xFF) / 255f, b = (rgb & 0xFF) / 255f;
                if (EbxReflect.IsVecLike(t))
                {
                    var target = old ?? EbxReflect.Instance(t);
                    foreach (var p in EbxReflect.Fields(t).Where(p => p.PropertyType == typeof(float)))
                    {
                        var n = p.Name.ToLowerInvariant();
                        if (n == "x") p.SetValue(target, r, null);
                        else if (n == "y") p.SetValue(target, g, null);
                        else if (n == "z") p.SetValue(target, b, null);
                        else if (n == "w" && alpha >= 0) p.SetValue(target, alpha / 255f, null);
                    }
                    return target;
                }
                if (t == typeof(uint) || t == typeof(int) || t == typeof(ulong) || t == typeof(long))
                {
                    ulong oldBits = Bits(old);
                    ulong a = alpha >= 0 ? (ulong)alpha << 24 : (oldBits & 0xFF000000UL);
                    ulong packed = a | rgb;
                    if (t == typeof(uint)) return (uint)packed;
                    if (t == typeof(int)) return unchecked((int)(uint)packed);
                    if (t == typeof(ulong)) return packed;
                    return (long)packed;
                }
                throw new HarnessException("a colour can't be written to a " + t.Name + " field");
            }
            if (t == typeof(string)) return s;
            if (t.Name == "CString") return Activator.CreateInstance(t, s);
            if (t == typeof(bool)) return bool.Parse(s);
            if (t.IsEnum) return Enum.Parse(t, s, true);
            if (t == typeof(Guid)) return Guid.Parse(s);
            if (t.IsPrimitive) return System.Convert.ChangeType(s.StartsWith("0x") ? (object)System.Convert.ToUInt64(s.Substring(2), 16) : s, t, inv);
            throw new HarnessException("can't set a " + t.Name + " field from a string (point the path at one of its members)");
        }
    }
}
