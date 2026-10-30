"""Optional Qwen adapter. No model downloads during inference."""
import os
import importlib.util
from pathlib import Path

LANGUAGES = {"ru": "Russian", "en": "English", "de": "German", "fr": "French", "es": "Spanish", "it": "Italian", "pt": "Portuguese", "zh": "Chinese", "ja": "Japanese", "ko": "Korean"}


def is_cuda_oom(exc):
    # Match PyTorch CUDA errors, not unrelated host memory or model failures.
    return (type(exc).__name__ == 'OutOfMemoryError' and type(exc).__module__.startswith('torch')) or bool(
        __import__('re').search(r'CUDA (?:out of memory|error: out of memory)', str(exc), __import__('re').I))


def select_backend(device, backend='auto'):
    if backend not in {'auto', 'standard', 'cuda_graphs'}:
        raise ValueError('Неизвестный режим озвучки')
    available = importlib.util.find_spec('faster_qwen3_tts') is not None
    if backend == 'auto':
        return 'cuda_graphs' if device == 'cuda:0' and available else 'standard'
    if backend == 'cuda_graphs' and (device != 'cuda:0' or not available):
        raise ValueError('Ускоренный режим требует NVIDIA CUDA и установленный faster-qwen3-tts 0.3.2')
    return backend


class QwenEngine:
    def __init__(self, model_directory: Path, device="cpu", backend='standard'):
        if not model_directory.is_dir() or not (model_directory / "config.json").is_file():
            raise ValueError("Сначала установите модель и укажите её локальную папку")
        if device not in {"cpu", "cuda:0", "mps"}:
            raise ValueError("Неизвестное устройство")
        self.backend = select_backend(device, backend)
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        try:
            import torch
            from qwen_tts import Qwen3TTSModel
        except ImportError as exc:
            raise RuntimeError("TTS-зависимости не установлены. Импорт книг продолжает работать") from exc
        if device=='cuda:0' and not torch.cuda.is_available():
            raise ValueError('CUDA недоступна в этой сборке PyTorch. Выберите CPU или установите проверенную CUDA-сборку')
        dtype=torch.float32
        if device=='cuda:0':
            dtype=torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
        if self.backend == 'cuda_graphs':
            torch.set_num_threads(min(6, os.cpu_count() or 1))
        self.model = Qwen3TTSModel.from_pretrained(str(model_directory.resolve()), device_map=device,
            dtype=dtype,
            attn_implementation="sdpa", local_files_only=True)
        self.prompts = {}
        self.fast = None

    def prepare_voice(self, reference: Path, transcript):
        if not reference.is_file():
            raise ValueError("Образец голоса не найден")
        key = (str(reference.resolve()), reference.stat().st_mtime_ns, transcript)
        if key not in self.prompts:
            self.prompts[key] = self.model.create_voice_clone_prompt(ref_audio=str(reference), ref_text=transcript or None, x_vector_only_mode=not bool(transcript))
        return self.prompts[key]

    def synthesize(self, text, language, reference: Path, transcript, output: Path):
        import soundfile as sf
        from .stress import speech_stress
        text = speech_stress(text, language)
        if language not in LANGUAGES:
            raise ValueError("Модель не поддерживает этот язык")
        prompt = self.prepare_voice(reference, transcript)
        if self.backend == 'cuda_graphs':
            if self.fast is None:
                from .cuda_graphs import CudaGraphSynthesizer
                self.fast = CudaGraphSynthesizer(self.model)
            wavs, rate = self.fast.synthesize(text, LANGUAGES[language], prompt)
        else:
            wavs, rate = self.model.generate_voice_clone(text=text, language=LANGUAGES[language], voice_clone_prompt=prompt)
        sf.write(str(output), wavs[0], rate, subtype="PCM_16", format="WAV")
