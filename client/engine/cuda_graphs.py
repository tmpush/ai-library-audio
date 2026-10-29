"""Pinned, local-only CUDA-graph adapter. Imports heavy packages only when selected."""
import importlib.metadata
import time

FAST_VERSION = '0.3.2'
MAX_TOKENS = 512
MAX_CONTEXT = 1024


def require_complete(count, prefill, token_limit=MAX_TOKENS, context_limit=MAX_CONTEXT):
    if count <= 0:
        raise ValueError('Модель не создала аудио')
    if count >= token_limit or prefill + count >= context_limit:
        raise ValueError('Генерация достигла лимита: неполное аудио не сохранено. Разделите фрагмент или сократите образец голоса')


class CudaGraphSynthesizer:
    def __init__(self, base_model):
        import torch
        from faster_qwen3_tts import FasterQwen3TTS
        from faster_qwen3_tts.predictor_graph import PredictorGraph
        from faster_qwen3_tts.talker_graph import TalkerGraph
        if importlib.metadata.version('faster-qwen3-tts') != FAST_VERSION:
            raise ValueError('Для ускоренного режима нужна проверенная версия faster-qwen3-tts 0.3.2')
        self.torch = torch

        class BoundedPredictor(PredictorGraph):
            deadline = float('inf')

            def run(self, *args, **kwargs):
                if time.monotonic() > self.deadline:
                    raise TimeoutError('Фрагмент генерируется дольше 5 минут. Освободите GPU и повторите задачу')
                return super().run(*args, **kwargs)

        talker = base_model.model.talker
        config = base_model.model.config.talker_config
        device = str(talker.device)
        dtype = next(talker.parameters()).dtype
        self.predictor = BoundedPredictor(talker.code_predictor, talker.code_predictor.model.config,
                                         config.hidden_size, device=device, dtype=dtype)
        graph = TalkerGraph(talker.model, config, device=device, dtype=dtype, max_seq_len=MAX_CONTEXT)
        self.fast = FasterQwen3TTS(base_model, self.predictor, graph, device=device,
                                  dtype=dtype, max_seq_len=MAX_CONTEXT)

    def synthesize(self, text, language, prompt):
        import numpy as np
        from faster_qwen3_tts.generate import fast_generate
        torch = self.torch
        # Release unused allocator blocks between fragments, never within the token loop.
        torch.cuda.empty_cache()
        try:
            with torch.inference_mode():
                m, talker, config, inputs, mask, trailing, pad, reference = self.fast._prepare_generation(
                    text=text, language=language, voice_clone_prompt=prompt,
                    non_streaming_mode=False, append_silence=False)
                self.predictor.deadline = time.monotonic() + 300
                codes, self.last_timing = fast_generate(
                    talker=talker, talker_input_embeds=inputs, attention_mask=mask,
                    trailing_text_hiddens=trailing, tts_pad_embed=pad, config=config,
                    predictor_graph=self.predictor, talker_graph=self.fast.talker_graph,
                    max_new_tokens=MAX_TOKENS, min_new_tokens=2,
                    temperature=0.9, top_k=50, top_p=1.0, do_sample=True, repetition_penalty=1.05)
                count = 0 if codes is None else len(codes)
                require_complete(count, inputs.shape[1])
                combined = torch.cat([reference.to(codes.device), codes], dim=0) if reference is not None else codes
                waves, rate = m.speech_tokenizer.decode({'audio_codes': combined.unsqueeze(0)})
                wave = waves[0]
                if hasattr(wave, 'cpu'):
                    wave = wave.flatten().cpu().numpy()
                else:
                    wave = np.asarray(wave).flatten()
                if reference is not None:
                    wave = wave[int(len(reference) / len(combined) * len(wave)):]
                if not len(wave) or not np.isfinite(wave).all() or np.max(np.abs(wave)) < .0001:
                    raise ValueError('Модель вернула пустое или некорректное аудио')
                del combined, codes, waves, inputs, mask, trailing, pad, reference
                return [wave], rate
        finally:
            self.predictor.deadline = float('inf')
            torch.cuda.empty_cache()
