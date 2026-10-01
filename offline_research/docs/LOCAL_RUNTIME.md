# Portable local research-model runtime

Prepared September 24, 2026 Pacific / September 25 UTC. This document records acquisition and executable inspection. It does not claim that model inference, GPU loading, worker isolation or a research campaign has passed its acceptance tests.

## Purpose and scope

The project needs a worker that can propose bounded research actions using local inputs, without granting an agent access to network tools, a shell, the final holdout or trading endpoints. The selected candidate is the open-weight **gpt-oss-20b**, served by a portable standalone **llama.cpp completion process**. OpenAI describes gpt-oss-20b as a model for local and specialized use; it has 21 billion total parameters and 3.6 billion active parameters. [Official model documentation](https://developers.openai.com/api/docs/models/gpt-oss-20b).

The downloaded GGUF is **ggml-org's conversion of OpenAI's model**, not an OpenAI-hosted API model or a reproduction of OpenAI's internal research infrastructure. A local worker still needs task schemas, a bounded controller, deterministic experiments, separate criticism and replication, and enforced input permissions. Model text must never be executed as an arbitrary command.

No installer, operating-system package, service, scheduled task, global PATH change, GPU-driver update or global model cache was used. Runtime files stay under `external/llama_cpp/`; model weights and provenance stay under `data/models/`. Those directories are ignored project artifacts. Existing historical weather acquisition was left running separately.

## Pinned acquisition plan

The official llama.cpp release `v0.5.0` publishes a nightly-tag pointer. The corresponding binary release is **b11146**, source commit **7fe450e19305b828c199d602c23a8337aaa1f03b**. Its Windows x64 CUDA **12.4** build and matching CUDA runtime archive were selected together. They are portable application libraries; they do not install a driver.

The model repository is `ggml-org/gpt-oss-20b-GGUF`, pinned to commit **ef9b12f2ff56c69cf32153a02784e7a3c88bf524**. The selected file is `gpt-oss-20b-MXFP4.gguf`; the repository card identifies `openai/gpt-oss-20b` as the base model and `apache-2.0` as the license metadata. The actual HF LFS SHA-256 is used, not the smaller Git pointer object's ID.

| Artifact | Published bytes | Published SHA-256 |
|---|---:|---|
| llama-b11146-bin-win-cuda-12.4-x64.zip | 253,869,799 | `3c806a6ceccc3dae1c743ceb1a1fb2cce5b76f40bfbd4c6b7b8afb6ef45a5807` |
| cudart-llama-bin-win-cuda-12.4-x64.zip | 391,443,627 | `8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6` |
| gpt-oss-20b-MXFP4.gguf | 12,109,566,624 | `27cd6c432c7672cb812a92f611cf3ba7bbc35928262bb1e1253ff4ee6ae35901` |

The planned artifact transfer is **12,754,880,050 bytes**, approximately 12.755 GB, within the separately enforced **18 GB total transfer allowance**. Metadata transfers are also counted. Download resumes require an exact HTTP byte range and the pinned total length; a partial file cannot be promoted until its complete SHA-256 matches. The raw ZIPs remain available for auditing.

## Acquisition controls

`src/klax_lab/acquire_local_model.py` first retrieves the pinned official GitHub release metadata and the specific Hugging Face revision metadata. It compares names, commits, byte lengths and published SHA-256 values against the fixed plan before downloading executable/model artifacts.

Only HTTPS hosts in the explicit GitHub/Hugging Face delivery allowlist are followed. Redirect destinations are checked before requesting them; signed CDN query strings are not included in provenance manifests. The records retain the original pinned source URL and redirect host names. No credential or Hugging Face token is required or written.

Each ZIP is hash-verified before extraction. Extraction rejects absolute paths, parent traversal, drive prefixes, alternate data streams, device names, symlinks/reparse entries, duplicate case-insensitive paths and targets outside the intended runtime directory. Member count and total uncompressed size are bounded. Existing files must match the verified archive; they are not silently replaced. An inventory records the SHA-256 of every extracted file, including implementation DLLs and CUDA libraries.

The transfer counter persists at `data/models/provenance/transfer_usage.json`. Acquisition outputs and status are recorded under `data/models/provenance/`. The operational log is `data/local_runtime_acquisition.log`.

## Verified native runtime interface

Both downloaded ZIPs passed their published hashes and were extracted to:

```text
external/llama_cpp/b11146-cuda12.4/
```

The primary candidate executable is:

```text
external/llama_cpp/b11146-cuda12.4/llama-completion.exe
```

The launcher is small and loads `llama-completion-impl.dll` and common libraries. Downstream integrity checks must bind the executable **and its verified DLL inventory**, not just the launcher.

After archive and executable verification, exactly these inspection operations were run, each with exit code 0:

```text
llama-completion.exe --version
llama-completion.exe --help
llama-cli.exe --version
llama-cli.exe --help
```

Both reported `0.5.0-dev`, build `11146`, commit `7fe450e19`, built with Clang 20.1.8 for Windows x86_64. Full output is saved at:

```text
external/llama_cpp/b11146-cuda12.4/inspection/llama-completion.version.txt
external/llama_cpp/b11146-cuda12.4/inspection/llama-completion.help.txt
external/llama_cpp/b11146-cuda12.4/inspection/llama-cli.version.txt
external/llama_cpp/b11146-cuda12.4/inspection/llama-cli.help.txt
external/llama_cpp/b11146-cuda12.4/inspection/inspection-manifest.json
external/llama_cpp/b11146-cuda12.4/verified-inventory.json
```

The completion help explicitly includes `--offline`, a local model path, prompt/file inputs, bounded context and token counts, seed and temperature settings, GPU-layer controls, single-turn operation, simple I/O, prompt-display control and JSON-schema constraints. Its `--offline` description says it prevents network access and forces local cache use. This application option supports the design; it is not itself an operating-system sandbox proof.

The worker adapter should use the standalone completion executable and a fixed argument allowlist. Model-download URLs, Hugging Face auto-fetch options, remote backends, server launch, arbitrary tool dispatch and shell interpolation must be unavailable to model-generated content. The controller must parse and validate the proposed action before deterministic code acts on it.

## Model status and remaining acceptance checks

The model acquisition completed at **2026-09-25 02:46:52 UTC**. All three artifacts matched their pinned published SHA-256 values, and the downloader exited successfully. The verified final model is at:

```text
data/models/gpt-oss-20b/ef9b12f2ff56c69cf32153a02784e7a3c88bf524/gpt-oss-20b-MXFP4.gguf
```

The authoritative completion manifest is `data/models/provenance/acquisition_20260925T024652949993Z.json`, with status `REQUESTED_ACQUISITION_COMPLETE`. Accounted transfer including source metadata was **12,754,966,428 bytes**, below the 18 GB allowance. A `.part` file is never a usable model; downstream workers must reverify the final model, launcher, DLL inventory and saved interface artifacts against their pinned hashes.

**No inference or server was started for this acquisition subtask.** The supplied hardware inventory reports an RTX 3090 with 24,576 MiB total VRAM, approximately 23,098 MiB free at inspection, 32 GB system memory and a Threadripper 3970X. This informed the candidate choice but does not prove a particular context length, throughput or memory configuration works. Bounded model loading, synthetic worker interactions, CUDA allocation, output parsing, stopping conditions and capability restrictions have separate controller acceptance records; consult the current implementation status for their results.

The later controller must specify a modest context/token budget instead of inheriting a large model default, reserve resources across workers, and serialize or explicitly budget GPU use. No paid API key or external inference service is configured by this acquisition.

## Sources

- [Official OpenAI gpt-oss-20b model documentation](https://developers.openai.com/api/docs/models/gpt-oss-20b).
- [Official llama.cpp gpt-oss implementation guide](https://github.com/ggml-org/llama.cpp/discussions/15396).
- [Official llama.cpp b11146 release](https://github.com/ggml-org/llama.cpp/releases/tag/b11146).
- [Pinned ggml-org model revision](https://huggingface.co/ggml-org/gpt-oss-20b-GGUF/tree/ef9b12f2ff56c69cf32153a02784e7a3c88bf524).
- Local source metadata, published hashes, extraction inventory and executable inspection output described above.
