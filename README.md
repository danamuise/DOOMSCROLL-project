# DOOMSCROLL
## What does social media discourse look like?

### Run the Windows desktop GUI

From the cloned repository directory, install dependencies once:

```powershell
python -m pip install -r requirements.txt
```

For a presentation-only preview (no API key or network required):

```powershell
python chatterfeed.py --demo
```

For the live, AI-interpreted social-media feed (requires the `OPENAI_API_KEY` environment variable):

```powershell
python chatterfeed.py --gui
```

Without these flags, `python chatterfeed.py` still starts the original command-line interface.

The desktop UI uses Python Tkinter (bundled with most Windows Python installations).
To display the project's custom pixel logo, put `DoomScrollLogo.png` in the
`assets` subfolder of the repository. Without it, a plain-text fallback logo appears.
The File / Settings / Status menu labels are placeholders.
