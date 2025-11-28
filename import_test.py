import importlib, traceback, sys
print("Trying to import module: tryitone.app")
try:
    importlib.import_module("tryitone.app")
    print("IMPORT OK: tryitone.app loaded successfully!")
except Exception:
    print("IMPORT FAILED: full traceback below")
    traceback.print_exc()
    sys.exit(1)
