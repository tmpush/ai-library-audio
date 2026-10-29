import hashlib
import json
import platform
import re
import shutil
import subprocess


def machine_id():
    if platform.system() == "Windows":
        result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", "(Get-CimInstance Win32_ComputerSystemProduct).UUID"], capture_output=True, text=True, timeout=15, check=True, creationflags=0x08000000)
        raw = result.stdout.strip()
    elif platform.system() == "Darwin":
        result = subprocess.run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"], capture_output=True, text=True, timeout=10, check=True)
        match = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', result.stdout)
        raw = match[1] if match else ""
    else:
        raise ValueError("Поддерживаются Windows и macOS")
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", raw) or set(raw.replace("-", "")) <= {"0", "F", "f"}:
        raise ValueError("Не удалось получить стабильный идентификатор компьютера")
    return hashlib.sha256(("local-books-order:v1:" + raw.lower()).encode()).hexdigest()


def inspect_hardware():
    result = {"platform": platform.system(), "architecture": platform.machine(), "devices": ["cpu"], "gpus": [], "warning": "Скорость CPU пока не измерена. Оценка времени появится после первого синтеза."}
    if shutil.which("nvidia-smi"):
        try:
            command = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10, check=True, **({"creationflags": 0x08000000} if platform.system() == "Windows" else {}))
            for i, line in enumerate(command.stdout.splitlines()):
                name, memory = line.rsplit(",", 1)
                result["gpus"].append({"name": name.strip(), "vram_mib": int(memory)})
                if i == 0:
                    result["devices"].append("cuda:0")
            if result["gpus"] and result["gpus"][0]["vram_mib"] >= 8192:
                result["warning"] = "Видеопамяти не меньше 8 ГБ. Фактическая скорость зависит от модели и будет измерена при синтезе."
            elif result["gpus"]:
                result["warning"] = "Меньше 8 ГБ видеопамяти: возможна нехватка памяти. Доступен CPU-режим."
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        result["devices"].append("mps")
        result["warning"] = "Apple Silicon обнаружен. MPS для выбранной модели ещё требует проверки; доступен CPU."
    return result
