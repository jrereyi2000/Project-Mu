#!/usr/bin/env bash
# Build MmcHarness.exe with Windows' own .NET Framework csc.exe (C# 5, x64), through WSL interop.
#
# References MMC Editor's DLLs in place (env MU_MMC_EDITOR_DIR, Windows form); nothing from the
# editor is copied. Output: C:\Madden27Modding\MuHarness\MmcHarness.exe (+ .exe.config), outside
# the repo (env MU_MMC_HARNESS_OUT overrides the output folder, Windows form). No artefacts are
# written into the repo.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
src="$here/MmcHarness.cs"

csc="/mnt/c/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe"
editor_win="${MU_MMC_EDITOR_DIR:-C:\\Madden27Modding\\MMC_Modding_Tools_v1.1.0.7\\MMC_Editor_v1.1.0.7}"
out_win="${MU_MMC_HARNESS_OUT:-C:\\Madden27Modding\\MuHarness}"
fw_win='C:\Windows\Microsoft.NET\Framework64\v4.0.30319'
wpf_win="$fw_win\\WPF"

[ -x "$csc" ] || { echo "build.sh: csc.exe not found at $csc" >&2; exit 1; }
editor_wsl="$(wslpath -u "$editor_win")"
[ -f "$editor_wsl/FrostySdk.dll" ] || { echo "build.sh: FrostySdk.dll not found in $editor_win (set MU_MMC_EDITOR_DIR)" >&2; exit 1; }
out_wsl="$(wslpath -u "$out_win")"
mkdir -p "$out_wsl"

src_win="$(wslpath -w "$src")"

refs=(
  "/reference:$editor_win\\FrostySdk.dll"
  "/reference:$editor_win\\FrostyCore.dll"
  "/reference:$editor_win\\FrostyHash.dll"
  "/reference:$editor_win\\Plugins\\TexturePlugin.dll"
  "/reference:$editor_win\\ThirdParty\\SharpDX.dll"
  "/reference:$editor_win\\ThirdParty\\SharpDX.DXGI.dll"
  "/reference:$editor_win\\ThirdParty\\SharpDX.Direct3D11.dll"
  "/reference:$wpf_win\\PresentationFramework.dll"
  "/reference:$wpf_win\\PresentationCore.dll"
  "/reference:$wpf_win\\WindowsBase.dll"
  "/reference:$fw_win\\System.Xaml.dll"
)

# Run csc from the output folder so nothing (not even a stray temp file) lands in the repo.
cd "$out_wsl"
"$csc" /nologo /target:exe /platform:x64 /optimize+ /langversion:5 \
  "/out:$out_win\\MmcHarness.exe" "${refs[@]}" "$src_win" | tr -d '\r'
status=${PIPESTATUS[0]}
[ "$status" -eq 0 ] || { echo "build.sh: csc failed ($status)" >&2; exit "$status"; }

# Runtime config: .NET 4.8 and large arrays (the asset index is big), as MMCEditor.exe.config has.
cat > "$out_wsl/MmcHarness.exe.config" <<'EOF'
<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <startup>
    <supportedRuntime version="v4.0" sku=".NETFramework,Version=v4.8" />
  </startup>
  <runtime>
    <gcAllowVeryLargeObjects enabled="true" />
  </runtime>
</configuration>
EOF

echo "build.sh: built $out_win\\MmcHarness.exe"
