# CardTraders backend package
import os
try:
	from dotenv import load_dotenv
	# Load .env if present (in backend folder or project root)
	load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))
	load_dotenv()  # also try default locations
except Exception:
	# dotenv is optional; continue if not installed
	pass
