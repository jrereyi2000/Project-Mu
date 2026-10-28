# Replacing Gemini 3.8 Flash with Open Models: Research (2026-10-28)

Can Project Mu replace `gemini-3.8-flash` with an open-weight model? This covers running one locally on the PC's RTX 5070 Ti (16GB), and calling a hosted open model through an API. It extends `docs/PC_MIGRATION_PLAN.md` §7 and corrects it in a few places (§6 below). Research only; nothing is built.

**Sourcing:** model specs and benchmarks below are mostly vendor-reported or from aggregator sites, collected on 2026-10-28. Treat speeds and VRAM figures as estimates until measured on the PC. Project Mu's own numbers come from `llm_usage`.

---

## 1. Bottom line

1. **The cost case for a local model is the newsroom, but the newsroom can't run locally.** Articles are ~75% of the post-migration Gemini bill. Their 25–184K-token tool loops don't fit next to a model's weights on 16GB.
2. **Gemini 3.8 Flash doubles in price on 2027-01-01** ($0.75 → $1.50 input, $3.75 → $7.50 output and thinking, per Google's pricing page). That lands around the PC migration, and it makes hosted open models worth testing for the newsroom. The plan doesn't consider this.
3. **For the short tasks, most of the cost is thinking.** 88–94% of their output tokens are thinking. Capping thinking on Gemini (plan §7.9) cuts most of their cost now, with no hardware. After that, the reasons to run them locally are no rate limits, offline use and V2 volume, not saving money.
4. **On 16GB, the best local candidates are mixture-of-experts (MoE) models with 2–4B active parameters:** Gemma 4 26B-A4B, LFM2.5-24B-A2B, and Qwen3.6-35B-A3B with experts offloaded to system RAM. The dense alternative is Qwen3.8-27B.
5. **LFM3 (released yesterday) is too small to write in a persona's voice, but it fits beside Madden.** At ~1GB it can run while Madden has the GPU. That makes it a candidate for classification and structured-output calls (GM JSON, commission classification) during play, which plan §7.5 currently sends back to Gemini.

---

## 2. What Project Mu sends to Gemini 3.8 Flash today

From `llm_usage`, 2026-10-22 – 2026-10-27 (6 days), all `gemini-3.8-flash`. Costs use cached input at 10% of the input price and count thinking at the output price.

| Source | Calls/day | $/day now | $/day from 2027-01-01 | Thinking share of output | After the migration |
| :--- | ---: | ---: | ---: | ---: | :--- |
| `newsroom` | ~340 | $3.89 | $7.78 | 64% | stays (Gemini or a hosted open model) |
| `extract` (OCR) | ~560 | $2.06 | $4.11 | 11% | retired by the save loader |
| `agent_wire` | ~170 | $0.86 | $1.71 | 94% | local candidate |
| `agent_memory` | ~83 | $0.48 | $0.96 | 88% | local candidate |
| `scouting_notes` | ~17 | $0.08 | $0.15 | 93% | local candidate |
| `gm_*` | <5 | ~$0.01 | ~$0.02 | ~87% | local candidate |

* After the migration (no `extract`): **~$5.3/day (~$160/month) now, ~$10.6/day (~$320/month) in 2027.** The newsroom is ~73% of that.
* Short tasks with thinking off: their output-plus-thinking bill drops ~90%, to roughly **$0.2/day** at 2027 prices. Local hardware can't save much more than that.
* Newsroom input is ~86% cache hits (`swarm_dispatcher.py`'s tool loop), so a provider's cache-hit price matters more than its headline input price.

---

## 3. The 16GB local tier

What fits on the RTX 5070 Ti with room for 2–5K-token prompts. "Fits" assumes a 4-bit GGUF (Q4_K_M or similar) in Ollama or llama.cpp.

| Model | Released | Params (total / active) | License | Q4 size | Notes for Project Mu |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Gemma 4 26B-A4B** | 2026-03-31 | ~26B / 4B, MoE | Apache 2.0 | ~14.5–16GB (reports vary) | Strongest MoE that fits. Google-reported MMLU-Pro 82.6, GPQA 82.3. ~130 tok/s reported on a 5070 Ti. Tight in 16GB, so it may need a 3-bit quant or some experts on CPU. Same family as Gemini, which may help tone carry over. Ollama tag `gemma4:26b`. |
| **Qwen3.8-27B** | 2026-08-14 | 27B dense | Apache 2.0 | ~16–17GB; needs ~3.5-bit to fit | Strongest dense model at this size. Creative Writing v3 ~1671 Elo (one aggregator). Dense, so ~40 tok/s rather than 100+. 262K native context. |
| **LFM2.5-24B-A2B** (Liquid) | 2026-10-13 | 24B / 2.45B, MoE | LFM Open License v1.0 (free under $10M revenue) | ~14GB | Liquid claims it leads Qwen3.6-35B-A3B and Gemma 4 26B-A4B on instruction following (IFEval 96.8, IFBench 74.7) and tool use. Not independently checked yet. Very fast (184 tok/s on M5 Max). |
| **Qwen3.6-35B-A3B** | 2026-04-16 | 35B / 3B, MoE | Apache 2.0 | ~20.6GB, doesn't fit | Runs with `--n-cpu-moe` (experts in system RAM). Reports put it at tens of tok/s on a 16GB card. |
| **Nemotron 3 Nano** (NVIDIA) | 2026 | 30B / ~3B, MoE | NVIDIA open model license | ~18GB, doesn't fit | Same offload approach. Built for instruction following and tool calling; up to 1M context. |
| **gpt-oss-20b** (OpenAI) | 2025-08 | 21B / 3.6B, MoE | Apache 2.0 | fits ("within 16GB", per OpenAI) | Fast (~189 tok/s reported on a 5070 Ti). Older, and known for flat prose. Useful as a baseline. |
| **Gemma 4 E4B / E2B** | 2026-03-31 | ~4B / ~2B effective | Apache 2.0 | 3–5GB | Small enough to run beside Madden. |
| **LFM3-1.7B** (Liquid) | **2026-10-27** | 1.73B | LFM Open License v1.0 | **~1GB** (128K KV cache 1.6GB) | See §3.1. |

Not local candidates: **Mistral Small 4** is a 119B MoE despite the name. **Nemotron 3.5 Bolt 74B-A9B** targets 24GB at 2-bit. **MiniCPM-V 4.7 35B-A3B** is vision-first.

### 3.1 LFM3 (Liquid AI, 2026-10-27)

* Six ~2B models on one 1.7B base (LFM3-1.7B-Base, 22T pretraining tokens): text, VL-2B, Audio-2B, Omni-2B, and two "decision" models (d1-2B, d1-omni-2B) that return calibrated answers instead of text.
* "Tri-hybrid" backbone (gated short convolutions, sliding-window attention, global attention). 256K context, 16 languages. Day-one GGUF builds for llama.cpp and Ollama, plus vLLM, SGLang and MLX.
* Liquid's numbers for the 1.7B text model: IFEval 96.7, IFBench 73.0, BFCL v4 71.4, GPQA Diamond 63.4. The model card says it isn't recommended for complex multi-step reasoning. No third-party scores yet (no Artificial Analysis entry as of today).
* **Fit for Project Mu:**
  * Agent Wire tweets and memory takes: unlikely. 192 persona voices, the anti-cliché rules and no-wrong-numbers facts are a lot to ask of 1.7B. Worth including in the side-by-side only as a floor.
  * GM structured output (`gm_negotiation`, rationale JSON) and `custom_commission` classification: plausible. Its strengths are instruction following and tool calling, and at ~1GB it can stay loaded while Madden runs. That would keep live negotiations local during play, where plan §7.5 currently falls back to Gemini.
  * The audio and omni variants are worth a look for the broadcast layer (plan §9) and V2.6 radio, as small local speech models.

### 3.2 MoE offload changes the 16GB math

Plan §7.2 says system RAM is too slow for offload to help. That holds for dense models, not for MoE. With llama.cpp's `--n-cpu-moe` / `--cpu-moe`, attention and shared weights stay on the GPU and only the per-token experts are read from RAM. A 30–35B model with 3B active then runs at usable speeds (one report: a 30B-A3B went from ~10 to ~43 tok/s on a much slower 16GB T4 once an expert cache was added). With 32GB of DDR5 this opens the 30–35B MoE tier. With 24GB it's tight beside Windows and Docker.

---

## 4. The frontier open tier (hosted, can't run on the PC)

These are the "SOTA" open-weight models. None runs on 16GB; they matter as **hosted APIs** that could take over the newsroom, which no local model can.

| Model | Params (total / active) | License | API price per 1M (input / cache hit / output) | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **DeepSeek V4.1 Flash (1017)** | 552B / 8–16B, MoE | MIT | $0.30 / very low / $1.20 at peak; half that off-peak | Updated 2026-10-17; 1M context. 3rd on Artificial Analysis's open-weights index (45.1). Off-peak pricing suits overnight newsroom runs. |
| **GLM-5.3-Flash** (Z.ai) | 320B / 18B | MIT | $0.15 / $0.03 / $0.50 | Cheapest credible option; AA index 41.8. |
| **GLM-5.3** (Z.ai) | n/a | custom | $1.40 / – / $4.40 | AA index 44.8; often named best for most work. |
| **Kimi K3** (Moonshot) | 2.8T / 104B | Kimi K3 license | $3.00 / $0.30 / $15.00 | Strongest on agentic and coding benchmarks, but **4× Gemini 3.8 Flash's current price**, so not a cost replacement. Its predecessor, Kimi K2.6, is the top open model on EQ-Bench creative writing (~1780 Elo); K3 isn't scored there yet. |
| **MiMo-V2.6-Pro** (Xiaomi) | n/a | n/a | n/a | #1 open model on BenchLM and Artificial Analysis (46.3) as of 10-19, but BenchLM marks its score estimated. |
| **Qwen3.8-2.4T-A95B / Qwen3.8 Max** | 2.4T / 95B | Qwen license | n/a | Whether Max is open-weight is disputed between the two leaderboards. |
| **Qwen3.8-Flash-Next** | 125B / 6B | Qwen Community | n/a | Experimental preview. |
| **Ling-3.1-flash** (Ant), **Beam 1.0** (Reflection), **Muse Spark Open** (Meta) | 560B/25B, 501B/23B, 816B/42B | MIT / Apache 2.0 / Meta | n/a | Other frontier-tier open releases. |
| Coming soon | | | | **Mistral Large 4** (weights listed for 2026-10-31); **MiniMax M3.1 Flash** (API live, weights "later"). |

All of the top nine leaderboard rows overlap within their confidence ranges, so the ranking among them isn't settled.

**Rough newsroom cost at today's volume** (~2.4M uncached input, ~15M cached input, ~0.32M output+thinking per day):

| Provider | $/day | $/month |
| :--- | ---: | ---: |
| Gemini 3.8 Flash, now | ~$4.1 | ~$125 |
| Gemini 3.8 Flash, 2027 | ~$8.2 | ~$245 |
| DeepSeek V4.1 Flash, peak (cache hit assumed ~$0.03) | ~$1.6 | ~$45 |
| GLM-5.3-Flash | ~$1.0 | ~$30 |
| Gemini 3.5 Flash-Lite (no switch needed; already compared in `tools/social_model_compare.py`) | ~$1.9 | ~$55 |

**Switching cost:** the newsroom's tool loop runs on the `google-genai` SDK's automatic function calling (`newsroom/swarm_dispatcher.py`), metered by `db/llm_usage.UsageMeter`, which wraps the genai client. DeepSeek, GLM, Kimi and Ollama all expose OpenAI-compatible APIs. Moving the newsroom means porting that loop and the meter to a second client, not swapping a model string. The short tasks are easier: `engines/gm_llm.make_llm` is already a prompt → text seam, and `agent_memory.gemini_text` and `social_writer.write_post` are single calls.

### 4.1 Do they match Flash for Project Mu's tasks?

**Features: yes. Benchmark scores: about level. Persona voice and accuracy over long tool loops: not yet known.** DeepSeek V4.1 Flash and GLM-5.3-Flash cover every Gemini feature Project Mu uses. Kimi K3 is the strongest of the three but costs ~4× Flash.

What the code uses: the newsroom (`swarm_dispatcher.py`) runs a chat with a system prompt, up to 50 automatic tool calls, then fix-up messages in the same chat. Its prompts are 45K tokens at the median, 72K at the 90th percentile and 150K at most (`llm_usage`, 10-22 – 10-27), with ~86% of input served from cache. `social_writer.py` sets a thinking level and uses an explicit cache. `custom_commission.py` forces a JSON schema. The GM calls are plain text at a set temperature (0.7; agent memory uses 0.3).

| Need | Gemini 3.8 Flash | DeepSeek V4.1 Flash | GLM-5.3-Flash | Kimi K3 |
| :--- | :--- | :--- | :--- | :--- |
| Context (needs ~150K) | 1M | 1M | ~1M | 1M |
| Tool calling | ✓, the SDK runs the loop | ✓ OpenAI format; you run the loop | ✓ OpenAI format, parallel calls | ✓ OpenAI format |
| Cache discount | implicit and explicit | automatic (one test saw 87% hits) | ✓ ($0.03 per 1M cached) | ✓ ($0.30 per 1M cached) |
| Forced JSON schema | strict | schema reportedly supported; plain JSON mode for sure | plain JSON mode for sure; schema unclear | ✓ |
| Thinking control | levels, can turn off | low / high / max, on by default | may always be on (sources conflict) | not checked |
| Temperature | ✓ | ignored while thinking is on | ✓ | ✓ |

**Porting work:**
* **Tool loop.** Write the newsroom's loop by hand (about 50 lines) in place of the SDK's automatic one. DeepSeek says to send each turn's reasoning back with tool results, or tool calls get worse.
* **Usage meter.** `db/llm_usage.UsageMeter` needs a version for the second client.
* **JSON.** Custom commissions validate the JSON and retry, instead of relying on a strict schema.
* **Thinking and temperature.** Turn thinking off on the short tasks. On DeepSeek that's also the only way to keep the GM and memory temperatures. GLM-5.3-Flash may not let you turn thinking off, though its output is cheap ($0.50 per 1M tokens).

**Performance:**
* **Artificial Analysis Intelligence Index (v4.3.2):** GLM-5.3-Flash 42, Gemini 3.8 Flash (High) 41, DeepSeek V4.1 Flash (Max) 39. On index v5 (released 10-28), DeepSeek V4.1 Flash and full GLM-5.3 top the open models at 32; Gemini 3.8 Flash has no v5 score yet.
* **Kimi K3 vs Gemini 3.8 Flash** (vendor-reported): Terminal-Bench 2.1 88.3 vs 90.8, GPQA Diamond 93.5 vs 95.4.
* **Persona writing:** on EQ-Bench creative writing, the Kimi family is the best open model, with GLM-5.x and DeepSeek V4 Pro close behind. There's no reliable score for the flash-tier open models or for Gemini 3.8 Flash, so there's no published evidence on the 192 voices.
* **Accuracy over a 50-call loop:** no public benchmark measures this the way the newsroom does it. This is the largest risk, and only the side-by-side week in §5 step 2 answers it.

**Running them:**
* **Where requests go:** DeepSeek's and Z.ai's own APIs run in China. The weights are MIT-licensed, so US hosts (DeepInfra, OpenRouter, Cloudflare Workers AI) serve the same models, sometimes at a different price.
* **DeepSeek's peak hours:** prices double on weekdays from 01:00–04:00 and 06:00–10:00 UTC. Overnight newsroom runs in US time zones fall mostly outside those hours.
* **Sources disagree** on GLM's thinking toggle and DeepSeek's effort levels. Check the official API docs before porting.

---

## 5. Recommended evaluation

Plan §7.4 already describes the quality bar (voice, facts, structure, thinking). This is what to put into it:

1. **Now, no hardware:** cap thinking on the short Gemini calls (plan §7.9). Then run `tools/social_model_compare.py` with `gemini-3.8-flash:low` and `gemini-3.5-flash-lite` to set the bar the open models have to meet.
2. **Hosted open models, now:** run one week of newsroom articles through DeepSeek V4.1 Flash and GLM-5.3-Flash (and Kimi K3 as a quality ceiling) and compare them side by side with Gemini. That needs the OpenAI-compatible port of the tool loop from §4; the comparison decides whether the port is worth keeping. Check especially: tool-call reliability over dozens of steps, persona voice, and fact accuracy against the data tools.
3. **Local short tasks, on the Mac now (plan §2.5) and again on the PC:** Agent Wire, memory and scouting prompts through Gemma 4 26B-A4B, Qwen3.8-27B, LFM2.5-24B-A2B and gpt-oss-20b, each with thinking off and on.
4. **Beside-Madden tier, on the PC:** GM JSON and commission classification through LFM3-1.7B and Gemma 4 E4B while Madden is running. Measure the GPU memory that's actually free and Madden's frame rate.
5. **Re-check before the switch:** this field changes monthly. Mistral Large 4, MiniMax M3.1 Flash weights and third-party LFM3 scores are all due within weeks.

---

## 6. Corrections to `docs/PC_MIGRATION_PLAN.md` §7

* §7.2 "System RAM … is not enough to make CPU offload of larger models useful": true for dense models, not for 30–35B MoE models with 3B active (§3.2).
* §7.3 "National articles: Gemini": add hosted open-weight models as a tested alternative, because of the 2027 price change (§1, §4).
* §7.5 "Queued jobs that can't wait … fall back to Gemini": a ~1–2GB model (LFM3, Gemma 4 E2B/E4B) may be able to stay loaded beside Madden for structured-output calls (§3.1).

## Sources

* Gemini pricing: [ai.google.dev/gemini-api/docs/pricing](https://ai.google.dev/gemini-api/docs/pricing)
* LFM3: [Unite.AI](https://www.unite.ai/liquid-ai-launches-lfm3-family-of-six-open-weight-2b-class-models/), [OrcaRouter](https://www.orcarouter.ai/blog/lfm3-family-release-tri-hybrid), [DEV Community](https://dev.to/paullovelace/liquid-ais-lfm3-six-2b-open-models-one-shared-brain-and-a-1gb-phone-sized-agent-40co)
* LFM2.5-24B-A2B: [OrcaRouter](https://www.orcarouter.ai/de/blog/lfm2-5-24b-a2b-release), [BenchmarkList](https://benchmarklist.com/models/liquid-lfm-2.5-24b-a2b/)
* Open-model landscape: [llms.blog (October picks)](https://www.llms.blog/posts/best-open-source-llms-in-2026-the-open-weight-models-worth-using-as-of-october-2026), [leaderboard audit (DEV)](https://dev.to/ward_ed_6b5e6aa8ded94a987/best-open-weight-llm-in-october-2026-auditing-two-leaderboards-row-by-row-1b64), [BenchLM open-source](https://benchlm.ai/best/open-source)
* Gemma 4: [Google release log](https://ai.google.dev/gemma/docs/releases?hl=en), [willitrunai](https://willitrunai.com/models/gemma-4-26b-a4b), [markaicode Ollama benchmarks](https://markaicode.com/benchmarks/gemma-4-ollama-benchmark/)
* Qwen3.8: [QwenLM/Qwen3.8](https://github.com/QwenLM/Qwen3.8), [Codersera lineup](https://codersera.com/blog/qwen-3-8-model-lineup-2026/)
* 16GB fit and MoE offload: [openclawdc (5070 Ti)](https://openclawdc.com/blog/best-local-llm-rtx-5070/), [llamaperf](https://llamaperf.com/gpu/rtx-5070-ti), [MoE cache on T4 (DEV)](https://dev.to/sampop3/llamacpps-moe-cache-nearly-doubled-30b-decode-speed-on-a-free-t4-15np), [compute-market](https://www.compute-market.com/blog/run-large-moe-models-small-gpu-2026)
* Mistral Small 4 / Nemotron: [Artificial Analysis on X](https://x.com/ArtificialAnlys/status/2034960206736892365?lang=en), [Nemotron (Wikipedia)](https://en.wikipedia.org/wiki/Nemotron)
* API pricing: [DeepSeek (Yotta Labs)](https://www.yottalabs.ai/post/deepseek-v4-1-flash-pricing-specs-v4-pro-routing-2026), [BenchLM DeepSeek pricing](https://benchlm.ai/deepseek/api-pricing), [GLM-5.3-Flash (eesel)](https://www.eesel.ai/blog/glm-5-3-flash-pricing), [Kimi K3 (BenchLM)](https://www.benchlm.ai/moonshot/api-pricing)
* Feature and benchmark parity (§4.1): [AA: Gemini 3.8 Flash vs GLM-5.3-Flash](https://artificialanalysis.ai/models/comparisons/gemini-3-8-flash-vs-glm-5-3-flash), [AA: DeepSeek V4.1 Flash vs GLM-5.3-Flash](https://artificialanalysis.ai/models/comparisons/deepseek-v4-1-flash-vs-glm-5-3-flash), [AA Index v5](https://x.com/ArtificialAnlys/status/2115265133467635877), [DeepSeek V4.1 Flash on OpenRouter](https://openrouter.ai/deepseek/deepseek-v4.1-flash), [DataCamp DeepSeek tutorial](https://www.datacamp.com/tutorial/deepseek-v4-1-api-tutorial), [GLM-5.3-Flash on OpenRouter](https://openrouter.ai/z-ai/glm-5.3-flash), [DeepInfra GLM guide](https://deepinfra.com/blog/glm-5-3-flash-documentation-integration-guide), [Cloudflare Workers AI GLM](https://developers.cloudflare.com/workers-ai/models/glm-5.3-flash/), [DataCamp on Gemini 3.8 Flash](https://datacamp.com/blog/gemini-3-8-flash-cyber), [Kimi K3 (morphllm)](https://www.morphllm.com/kimi-k3)
* Creative writing: [EQ-Bench](https://eqbench.com/about.html), [Featherless](https://featherless.ai/blog/best-ai-for-creative-writing-2026), [llmlearner](https://llmlearner.com/rankings/creative-writing)
