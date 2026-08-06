# VisionPR 👁️🚀

**VisionPR** is an autonomous, multimodal AI workflow designed to translate engineering meeting videos, screen recordings, or visual bug reports directly into tested, ready-to-merge GitHub Pull Requests. 

Built for the **GDG Hackathon**, VisionPR bridges the gap between human visual communication and codebase execution using agentic workflows.

## 🌟 How It Works (The 5 Phases)

VisionPR operates through a sequential, 5-phase pipeline:

1. **Phase 1: Multimodal Extraction** 
   Parses input videos (`.mp4`) using OpenCV and Faster-Whisper to extract key visual frames and transcribed context. Gemini is utilized to filter for context-heavy frames.
2. **Phase 2: Codebase Mapping**
   Analyzes the local target repository to map dependencies and locate the exact files requiring modification.
3. **Phase 3: Agentic Code Generation**
   A CrewAI agentic system (powered by Groq LPUs) ingests the multimodal context and codebase map to write, replace, and locally compile the targeted code fixes.
4. **Phase 4: Human-in-the-Loop (HITL) Review Gate**
   Pauses the automated workflow to present the generated code and terminal build results to a human engineer for final approval before pushing.
5. **Phase 5: Automated GitHub PR Publisher**
   Automatically creates a new branch, commits the approved changes, pushes to the remote repository, and opens a formatted Pull Request via the GitHub API.

## 🛠️ Tech Stack

* **Core Runtime:** Python 3.10+
* **Package Manager:** `uv` (for ultra-fast dependency resolution)
* **Agentic Framework:** CrewAI, CrewAI-Tools
* **LLM / Inference:** Groq API (Fast Inference), Google Gemini API (Vision processing)
* **Computer Vision / Audio:** OpenCV, Faster-Whisper
* **Version Control:** PyGithub, Git CLI

## Phase 3 Execution Modes

VisionPR Phase 3 uses CrewAI for its Architect, Coder, and Reviewer agents. An
external LLM connection is optional for installation, testing, CI, and controlled
demonstration.

```text
Online mode  -> CrewAI agents backed by a supported LLM provider
Offline mode -> deterministic demo agents using the same Phase 3 contracts
```

`AgentFactory` selects the agent implementation family. In offline mode it
returns deterministic heuristic agents. In CrewAI mode it asks `LLMFactory` for a
CrewAI-compatible Gemini or Groq LLM and injects the same LLM into the
Architect, Coder, and Reviewer adapters.

Offline mode is not genuine AI reasoning. It demonstrates input loading, shared
agent contracts, safe repository reads, controlled writes, build execution,
deterministic planning, rule-based review, retry limits, and the Phase 4 handoff
without external API calls.

Offline mode cannot demonstrate natural-language code generation, arbitrary
repository modification, visual issue interpretation, or a real model
conversation.

### Environment Configuration

Copy `.env.example` if you need local configuration. Do not commit `.env`.

```dotenv
VISIONPR_MODE=auto
VISIONPR_LLM_PROVIDER=
VISIONPR_LLM_MODEL=
GEMINI_API_KEY=
GROQ_API_KEY=
RUN_LLM_TESTS=0
```

Supported `VISIONPR_MODE` values:

- `auto`: use CrewAI online mode when a supported API key exists; otherwise use
  offline demo mode.
- `offline`: always use deterministic offline demo mode.
- `crewai`: require a supported API key and use CrewAI online mode.

Supported online providers are only `gemini` and `groq`. Set
`VISIONPR_LLM_PROVIDER` to choose one explicitly, or leave it empty for automatic
detection from configured keys and model prefixes. When both Gemini and Groq
keys are configured, Gemini is selected first unless the provider or model
clearly selects Groq. Groq requires a team-tested `VISIONPR_LLM_MODEL`.

### Running Tests

```bash
python -m unittest discover -s tests -v
```

Normal tests do not require API keys, network access, or real LLM calls.

### Running The Demo

Deterministic local demo:

```bash
python -m scripts.run_phase3_demo
```

The demo defaults to offline mode so it never needs API keys. Explicit offline
mode is also supported:

```bash
VISIONPR_MODE=offline python -m scripts.run_phase3_demo
```

PowerShell:

```powershell
$env:VISIONPR_MODE="offline"
python -m scripts.run_phase3_demo
```

Forced CrewAI mode:

```bash
VISIONPR_MODE=crewai python -m scripts.run_phase3_demo
```

PowerShell:

```powershell
$env:VISIONPR_MODE="crewai"
python -m scripts.run_phase3_demo
```

Forced CrewAI mode requires a supported API key. Without one, the demo prints a
clear configuration error.

### Optional Real LLM Tests

Real model tests are disabled by default:

```bash
RUN_LLM_TESTS=1 python -m unittest discover -s tests -v
```

PowerShell:

```powershell
$env:RUN_LLM_TESTS="1"
python -m unittest discover -s tests -v
```

### Security Boundaries

Phase 3 build commands are allow-listed. The Coder receives only safe repository
tools: `safe_read_file`, `safe_write_file`, and `run_validated_build_plan`.

The workflow never permits arbitrary shell execution, never sends `.env`
contents to an LLM, never trusts LLM-produced paths without validation, never
lets the model choose the repository root, and never lets model approval
override deterministic safety checks.

## 📂 Project Structure

```text
VisionPR/
├── data/
│   ├── extracted_frames/    # AI-curated video frames
│   ├── input_videos/        # Drop target .mp4 files here
│   └── output_json/         # Inter-phase data handoffs
├── src/
│   ├── extract_video.py     # Phase 1
│   ├── codebase_mapper.py   # Phase 2
│   ├── crew_engine.py       # Phase 3
│   ├── hitl_review_gate.py  # Phase 4
│   ├── github_publisher.py  # Phase 5
│   └── tools.py             # Custom CrewAI Tools (Read/Write/Build)
├── .env.example             # Template for API keys
├── main.py                  # Pipeline orchestrator
├── requirements.txt         # Standard pip fallback
└── README.md
