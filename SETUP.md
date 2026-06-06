# Setup Instructions — ResolveMate

Follow these steps to run ResolveMate on your local machine.

---

## Prerequisites

- Python 3.11 or higher installed
- Git installed
- An LLM API key (OpenAI, Google Gemini, Groq, or Anthropic)

---

## Step 1: Clone the Repository

```bash
git clone https://github.com/pavankumarh14/AgenticWeb-Advocate.git
cd AgenticWeb-Advocate
```

---

## Step 2: Create Virtual Environment

**On macOS/Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
```

**On Windows:**
```bash
python -m venv venv
venv\Scripts\activate
```

---

## Step 3: Install Dependencies

```bash
pip install -r requirements.txt
```

---

## Step 4: Configure Environment Variables

Copy the example environment file:

```bash
cp .env.example .env
```

Edit `.env` and add your API key:

```
# Required: Choose your LLM provider
ADVOCATE_LLM_PROVIDER=openai

# Required: Your API key
OPENAI_API_KEY=sk-your-key-here

# Optional: Override default model
ADVOCATE_LLM_MODEL=gpt-4o-mini
```

**Supported Providers:**
- `openai` — Uses OpenAI GPT models
- `gemini` — Uses Google Gemini (set `GEMINI_API_KEY`)
- `groq` — Uses Groq fast inference (set `ADVOCATE_LLM_API_KEY`)
- `anthropic` — Uses Claude (set `ADVOCATE_LLM_API_KEY`)
- `ollama` — Local models, no API key needed

---

## Step 5: Run the Server

```bash
python server.py
```

You should see:
```
ResolveMate dashboard at http://localhost:8000  (Ctrl+C to stop)
```

---

## Step 6: Open the Dashboard

Open your browser and go to:

```
http://localhost:8000
```

---

## Step 7: Create Your First Case

1. Click **"New Case"** on the dashboard
2. Enter your goal (e.g., "Full refund for defective product")
3. Set your policy:
   - Target amount: Full refund you want
   - Minimum acceptable: Lowest amount you'll accept
   - Currency: INR, USD, etc.
4. Choose counterparty mode:
   - **Scripted**: Fast demo with pre-written replies (no API calls)
   - **LLM**: AI role-plays the company support rep
   - **Manual**: You type replies manually
5. Click **Create Case**

The agent will start negotiating automatically. Watch the transcript update in real-time.

---

## Troubleshooting

### Port Already in Use
```bash
# Use a different port
PORT=8080 python server.py
```

### No API Key Error
- Check your `.env` file exists and has the correct key
- Reload the virtual environment: `source venv/bin/activate`

### Import Errors
```bash
# Reinstall dependencies
pip install -r requirements.txt --force-reinstall
```

---

## Running Tests

```bash
# Run all tests
python -m pytest tests/

# Run with verbose output
python -m pytest tests/ -v
```

---

## Deployment on Render

1. Fork this repository on GitHub
2. Create a new Web Service on Render
3. Connect your GitHub repository
4. Set environment variables in Render dashboard
5. Deploy — Render will auto-detect `render.yaml`

---

## Need Help?

- Check the `/examples` folder for sample case JSON files
- Read `README.md` for architecture details
- Open an issue on GitHub for bugs or questions
