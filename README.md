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