"""Patch custom_weights_handler.py with LoRAManager registry from models/lora_soggetto*.json"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODELS = ROOT / "models"
HANDLER = ROOT / "custom_weights_handler.py"

TRIGGERS = {
    1: "soggetto_uno",
    2: "soggetto_due",
    3: "soggetto_tre",
    4: "soggetto_quattro",
    5: "soggetto_cinque",
}


def lora_ref(subject_num: int) -> str:
    path = MODELS / f"lora_soggetto{subject_num}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    output = data.get("output")
    if isinstance(output, dict) and output.get("version"):
        return str(output["version"])
    rw = data.get("replicate_weights")
    if rw:
        return str(rw)
    dest = data.get("destination", "")
    return dest


def build_registry_block() -> str:
    lines = [
        "        self._registry: Dict[str, LoRAConfig] = {",
    ]
    for n in range(1, 6):
        key = f"soggetto_{n}"
        ref = lora_ref(n)
        trig = TRIGGERS[n]
        lines.append(
            f'            "{key}": LoRAConfig(subject_id="{key}", lora_path_or_id="{ref}", trigger_word="{trig}", weight=0.95),'
        )
    lines.append("        }")
    return "\n".join(lines)


def main() -> None:
    text = HANDLER.read_text(encoding="utf-8")

    if "class LoRAConfig" not in text:
        insert_after = "class CheckpointType(Enum):"
        lora_classes = '''

@dataclass
class LoRAConfig:
    """Replicate Flux LoRA identity slot for a subject."""
    subject_id: str
    lora_path_or_id: str
    trigger_word: str
    weight: float = 0.95


class LoRAManager:
    """Registry of per-subject Flux LoRA weights (Replicate model version refs)."""

    def __init__(self) -> None:
'''
        # registry appended in second step
        idx = text.find(insert_after)
        if idx == -1:
            raise SystemExit("CheckpointType not found")
        # find end of CheckpointType enum block
        m = re.search(r"class CheckpointType\(Enum\):.*?\n\n", text, re.S)
        if not m:
            raise SystemExit("Could not locate CheckpointType block")
        text = text[: m.end()] + lora_classes + build_registry_block() + "\n\n    def get(self, subject_id: str) -> Optional[LoRAConfig]:\n        return self._registry.get(subject_id)\n\n    def list_subjects(self) -> List[str]:\n        return list(self._registry.keys())\n\n\n" + text[m.end() :]

    else:
        # replace existing _registry block
        text = re.sub(
            r"self\._registry: Dict\[str, LoRAConfig\] = \{.*?\n        \}",
            build_registry_block(),
            text,
            count=1,
            flags=re.S,
        )

    HANDLER.write_text(text, encoding="utf-8")
    print("Updated", HANDLER)
    print(build_registry_block())


if __name__ == "__main__":
    main()
