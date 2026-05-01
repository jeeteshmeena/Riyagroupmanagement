"""
fix_cerebras_model.py — one-time script to correct the Cerebras model name in DB
Run: python3 fix_cerebras_model.py
"""
import json, os, sys

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config_db.json")

try:
    with open(CONFIG_FILE, "r") as f:
        cfg = json.load(f)

    ai = cfg.get("ai_config", {})
    prov = ai.get("providers", {})
    cerebras = prov.get("cerebras", {})

    old_model = cerebras.get("model", "")
    if old_model == "llama-3.3-70b":
        cerebras["model"] = "llama3.3-70b"
        prov["cerebras"] = cerebras
        ai["providers"] = prov
        cfg["ai_config"] = ai
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
        print(f"✅ Fixed: '{old_model}' → 'llama3.3-70b'")
    elif old_model:
        print(f"ℹ️  Model is already: '{old_model}' — no change needed")
    else:
        print("ℹ️  No Cerebras model found in DB")
except FileNotFoundError:
    print("⚠️  config_db.json not found — model will use default from code")
except Exception as e:
    print(f"❌ Error: {e}")
