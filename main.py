import re
import os
import wave
import html
import uuid
import asyncio
import requests
import time
import random
from typing import List, Tuple, Dict, Optional
from dataclasses import dataclass
from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import astrbot.api.message_components as Comp
from astrbot.core.message.message_event_result import ResultContentType

try:
    from pydub import AudioSegment
    from pydub.silence import detect_leading_silence
    PYDUB_AVAILABLE = True
except ImportError:
    PYDUB_AVAILABLE = False
    logger.warning("[GenieTTS] pydub not installed, audio trimming disabled. Install with: pip install pydub")

@dataclass
class SessionState:
    """会话状态"""
    last_tts_time: float = 0.0  # 最后一次 TTS 时间
    last_tts_text: str = ""      # 最后一次 TTS 的文本


@register(
    "genie-tts",
    "victical",
    "基于 Genie TTS 的语音合成插件",
    "1.0.0",
    "https://github.com/yourusername/astrbot_plugin_genie-tts"
)
class GenieTTSPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        self.base_url = f"http://{config.get('server_host', '127.0.0.1')}:{config.get('server_port', 9999)}"
        self.character_name = config.get('character_name', 'misono_mika')
        self.initialized = False
        self.temp_dir = os.path.join(os.path.dirname(__file__), "temp_audio")
        
        # 高级控制配置
        self.global_enable: bool = bool(config.get('global_enable', True))
        self.enabled_sessions: List[str] = []
        self.disabled_sessions: List[str] = []
        self.prob: float = float(config.get('prob', 1.0))
        self.text_limit: int = int(config.get('text_limit', 200))
        self.cooldown: int = int(config.get('cooldown', 0))
        
        # 会话状态管理
        self._session_state: Dict[str, SessionState] = {}
        
        # 创建临时音频目录
        os.makedirs(self.temp_dir, exist_ok=True)
        
        logger.info(f"[GenieTTS] 插件初始化，TTS 服务器: {self.base_url}")
        logger.info(f"[GenieTTS] 全局开关: {self.global_enable}, 概率: {self.prob}, 长度限制: {self.text_limit}, 冷却: {self.cooldown}s")
        
        # 异步初始化 TTS 服务器
        asyncio.create_task(self._initialize_tts())

    async def _initialize_tts(self):
        """初始化 TTS 服务器，加载模型和参考音频"""
        try:
            # 加载角色模型
            load_payload = {
                "character_name": self.character_name,
                "onnx_model_dir": self.config.get('onnx_model_dir', '/models/misono_mika')
            }
            
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: requests.post(f"{self.base_url}/load_character", json=load_payload, timeout=30)
            )
            
            if response.status_code != 200:
                logger.error(f"[GenieTTS] 模型加载失败: {response.text}")
                return
            
            logger.info(f"[GenieTTS] 模型加载成功: {response.json().get('message', '')}")
            
            # 设置参考音频
            ref_audio_payload = {
                "character_name": self.character_name,
                "audio_path": self.config.get('ref_audio_path', '/models/misono_mika/prompt.wav'),
                "audio_text": self.config.get('ref_audio_text', '')
            }
            
            response = await loop.run_in_executor(
                None,
                lambda: requests.post(f"{self.base_url}/set_reference_audio", json=ref_audio_payload, timeout=30)
            )
            
            if response.status_code != 200:
                logger.error(f"[GenieTTS] 参考音频设置失败: {response.text}")
                return
            
            logger.info(f"[GenieTTS] 参考音频设置成功")
            self.initialized = True
            
        except Exception as e:
            logger.error(f"[GenieTTS] 初始化失败: {e}", exc_info=True)

    def _clean_text(self, text: str) -> Tuple[str, List[str]]:
        """
        简单清理文本，仅移除首尾空格
        返回: (清理后的文本, 参考文献列表)
        """
        references = []
        cleaned = text.strip()
        return cleaned, references

    async def _translate_to_chinese(self, text: str) -> str:
        """
        使用 LLM 将文本翻译成中文
        返回: 翻译后的中文文本
        """
        try:
            # 获取用于翻译的提供商
            provider = self._get_translation_provider()
            if not provider:
                logger.warning("[GenieTTS] 没有可用的翻译提供商")
                return ""
            
            prompt = "你是一个专业的翻译助手。请将以下文本翻译成简体中文，只返回翻译结果，不要有任何其他说明：\n\n" + text
            
            response = await provider.text_chat(
                prompt=prompt,
                session_id=None,
                contexts=[],
                image_urls=[],
                system_prompt=""
            )
            
            if response.role == "assistant":
                translation = response.completion_text.strip()
                logger.info(f"[GenieTTS] 翻译完成: {text[:50]}... -> {translation[:50]}...")
                return translation
            else:
                logger.warning("[GenieTTS] LLM 未返回翻译结果")
                return ""
                
        except Exception as e:
            logger.error(f"[GenieTTS] 翻译失败: {e}", exc_info=True)
            return ""

    def _get_translation_provider(self):
        """
        获取用于翻译的提供商
        优先级：配置的特定提供商 > 当前默认提供商 > 第一个可用提供商
        """
        # 1. 尝试使用配置中指定的提供商 ID
        provider_id = self.config.get('translation_provider_id', '').strip()
        if provider_id:
            provider = self.context.get_provider_by_id(provider_id)
            if provider:
                logger.info(f"[GenieTTS] 使用指定的翻译提供商: {provider_id}")
                return provider
            else:
                logger.warning(f"[GenieTTS] 找不到指定的提供商 ID: {provider_id}，尝试使用默认提供商")
        
        # 2. 使用当前默认提供商
        provider = self.context.get_using_provider()
        if provider:
            logger.info(f"[GenieTTS] 使用默认提供商进行翻译")
            return provider
        
        # 3. 尝试使用第一个可用提供商
        all_providers = self.context.get_all_providers()
        if all_providers and len(all_providers) > 0:
            provider = all_providers[0]
            logger.info(f"[GenieTTS] 使用第一个可用提供商: {provider.meta().id}")
            return provider
        
        return None

    def _sess_id(self, event: AstrMessageEvent) -> str:
        """获取会话ID"""
        try:
            gid = event.get_group_id()
            if gid:
                return f"group_{gid}"
        except:
            pass
        return f"user_{event.get_sender_id()}"

    def _is_session_enabled(self, sid: str) -> bool:
        """检查会话是否启用TTS"""
        if self.global_enable:
            return sid not in self.disabled_sessions
        return sid in self.enabled_sessions

    def _save_config(self):
        """保存配置到文件"""
        try:
            self.config['global_enable'] = self.global_enable
            self.config['prob'] = self.prob
            self.config['text_limit'] = self.text_limit
            self.config['cooldown'] = self.cooldown
            # AstrBotConfig 会自动保存
        except Exception as e:
            logger.warning(f"[GenieTTS] 保存配置失败: {e}")

    def _trim_silence(self, audio_path: str) -> str:
        """
        去除音频开头和结尾的静音部分
        返回: 处理后的音频文件路径
        """
        if not PYDUB_AVAILABLE:
            return audio_path
        
        try:
            audio = AudioSegment.from_wav(audio_path)
            
            # 检测开头和结尾的静音（低于 -40dB 视为静音）
            def detect_silence(audio_segment, silence_thresh=-40):
                return detect_leading_silence(audio_segment, silence_threshold=silence_thresh)
            
            start_trim = detect_silence(audio)
            end_trim = detect_silence(audio.reverse())
            
            duration = len(audio)
            trimmed = audio[start_trim:duration-end_trim]
            
            # 覆盖原文件
            trimmed.export(audio_path, format="wav")
            logger.info(f"[GenieTTS] 已去除静音: 开头 {start_trim}ms, 结尾 {end_trim}ms")
            
            return audio_path
        except Exception as e:
            logger.warning(f"[GenieTTS] 去除静音失败: {e}")
            return audio_path

    async def _generate_audio(self, text: str, retry_count: int = 0) -> str:
        """
        生成音频文件
        返回: 音频文件路径
        """
        if not self.initialized:
            raise Exception("TTS 服务器未初始化")
        
        if not text or len(text.strip()) == 0:
            raise Exception("文本内容为空")
        
        max_retries = self.config.get('retry_attempts', 3)
        
        try:
            tts_payload = {
                "character_name": self.character_name,
                "text": text,
                "split_sentence": self.config.get('split_sentence', True)
            }
            
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: requests.post(f"{self.base_url}/tts", json=tts_payload, timeout=60)
            )
            
            if response.status_code != 200:
                raise Exception(f"TTS 请求失败: {response.status_code} - {response.text}")
            
            raw_audio_data = response.content
            
            if len(raw_audio_data) < 1000:  # 音频过短，可能生成失败
                if retry_count < max_retries:
                    logger.warning(f"[GenieTTS] 音频过短({len(raw_audio_data)} 字节)，重试 {retry_count + 1}/{max_retries}")
                    await asyncio.sleep(1)
                    return await self._generate_audio(text, retry_count + 1)
                else:
                    raise Exception(f"音频生成失败: 数据过短({len(raw_audio_data)} 字节)")
            
            # 保存为 WAV 文件
            filename = f"tts_{uuid.uuid4().hex}.wav"
            filepath = os.path.join(self.temp_dir, filename)
            
            # WAV 参数
            CHANNELS = 1
            SAMPWIDTH = 2
            FRAMERATE = 32000
            
            with wave.open(filepath, 'wb') as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(SAMPWIDTH)
                wf.setframerate(FRAMERATE)
                wf.writeframes(raw_audio_data)
            
            logger.info(f"[GenieTTS] 音频生成成功: {filepath} ({len(raw_audio_data)} 字节)")
            
            # 去除静音
            filepath = self._trim_silence(filepath)
            
            return filepath
            
        except Exception as e:
            if retry_count < max_retries:
                logger.warning(f"[GenieTTS] 生成失败，重试 {retry_count + 1}/{max_retries}: {e}")
                await asyncio.sleep(1)
                return await self._generate_audio(text, retry_count + 1)
            else:
                logger.error(f"[GenieTTS] 音频生成失败(已重试{retry_count}次): {e}", exc_info=True)
                raise

    @filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent, *args):
        """在发送消息前，将文本结果转换为语音"""
        try:
            if not self.initialized:
                return

            # 获取会话ID
            sid = self._sess_id(event)
            
            # 1. 检查会话是否启用
            if not self._is_session_enabled(sid):
                logger.info(f"[GenieTTS] 会话 {sid} TTS 未启用，跳过")
                return

            result = event.get_result()
            if not result or not result.chain:
                return

            # 检查是否为 LLM 响应
            try:
                is_llm_response = False
                try:
                    is_llm_response = result.is_llm_result()
                except:
                    is_llm_response = (getattr(result, "result_content_type", None) == ResultContentType.LLM_RESULT)
                
                if not is_llm_response:
                    logger.info("[GenieTTS] 非 LLM 响应，跳过 TTS")
                    return
            except:
                pass

            # 从 Plain 组件中提取所有文本
            text_to_convert = ""
            plain_component_indices = []
            for i, component in enumerate(result.chain):
                if isinstance(component, Comp.Plain):
                    text_to_convert += component.text + " "
                    plain_component_indices.append(i)
            
            text_to_convert = text_to_convert.strip()

            if not text_to_convert or len(text_to_convert) < 2:
                return

            # 2. 概率门控
            if random.random() > self.prob:
                logger.info(f"[GenieTTS] 概率门控未通过 (prob={self.prob})，跳过")
                return

            # 4. 长度限制
            if self.text_limit > 0 and len(text_to_convert) > self.text_limit:
                logger.info(f"[GenieTTS] 文本过长 ({len(text_to_convert)} > {self.text_limit})，跳过")
                return

            # 5. 冷却机制
            state = self._session_state.setdefault(sid, SessionState())
            now = time.time()
            if self.cooldown > 0 and (now - state.last_tts_time) < self.cooldown:
                logger.info(f"[GenieTTS] 冷却中 ({now - state.last_tts_time:.1f}s < {self.cooldown}s)，跳过")
                return

            logger.info(f"[GenieTTS] 开始处理: '{text_to_convert[:50]}...'")

            # 生成音频
            audio_path = await self._generate_audio(text_to_convert)

            # 更新会话状态
            state.last_tts_time = now
            state.last_tts_text = text_to_convert

            # 创建一个新的 Record 组件
            record_component = Comp.Record(file=audio_path, url=audio_path)

            # 用一个 Record 组件替换所有 Plain 组件
            for i in sorted(plain_component_indices, reverse=True):
                del result.chain[i]
            
            # 在第一个 Plain 组件的位置插入 Record 组件
            if plain_component_indices:
                result.chain.insert(plain_component_indices[0], record_component)
                
                # 如果配置了同时发送文本，在语音后添加中文翻译
                if self.config.get('send_text_with_audio', False):
                    translation = await self._translate_to_chinese(text_to_convert)
                    if translation:
                        result.chain.insert(plain_component_indices[0] + 1, Comp.Plain(f"\n[中文翻译]\n{translation}"))

            # 安排临时文件删除
            async def cleanup_file(path):
                await asyncio.sleep(10)
                try:
                    if os.path.exists(path):
                        os.remove(path)
                        logger.info(f"[GenieTTS] Cleaned up temp file: {path}")
                except Exception as e:
                    logger.warning(f"[GenieTTS] Failed to cleanup temp file {path}: {e}")
            
            asyncio.create_task(cleanup_file(audio_path))

        except Exception as e:
            logger.error(f"[GenieTTS] Failed to decorate result with TTS audio: {e}", exc_info=True)

    @filter.command("gentts")
    async def gentts_command(self, event: AstrMessageEvent, subcommand: str = "", text: str = ""):
        """Genie TTS 命令: gentts <test|on|off|status|globalon|globaloff> [文本]"""
        subcommand = subcommand.lower().strip()
        
        # test 子命令 - 测试语音生成
        if subcommand == "test":
            try:
                if not self.initialized:
                    yield event.plain_result("TTS 服务器未就绪，请稍后再试")
                    return
                
                if not text or len(text.strip()) == 0:
                    yield event.plain_result("请提供要转换的文本: gentts test <文本>")
                    return
                
                cleaned_text, references = self._clean_text(text)
                
                if not cleaned_text or len(cleaned_text.strip()) < 2:
                    yield event.plain_result("文本内容过短或无效")
                    return
                
                yield event.plain_result(f"正在生成语音...")
                
                audio_path = await self._generate_audio(cleaned_text)
                
                yield event.chain_result([
                    Comp.Record(file=audio_path, url=audio_path)
                ])
                
                if self.config.get('show_references', False) and references:
                    ref_text = "\n".join(references)
                    yield event.plain_result(f"[参考信息]\n{ref_text}")
                
                try:
                    await asyncio.sleep(5)
                    if os.path.exists(audio_path):
                        os.remove(audio_path)
                except Exception as e:
                    logger.warning(f"[GenieTTS] 清理临时文件失败: {e}")
                    
            except Exception as e:
                logger.error(f"[GenieTTS] 手动 TTS 失败: {e}", exc_info=True)
                yield event.plain_result(f"语音生成失败: {str(e)}")
        
        # on 子命令 - 启用会话 TTS
        elif subcommand == "on":
            sid = self._sess_id(event)
            if self.global_enable:
                if sid in self.disabled_sessions:
                    self.disabled_sessions.remove(sid)
            else:
                if sid not in self.enabled_sessions:
                    self.enabled_sessions.append(sid)
            yield event.plain_result("✅ 本会话 TTS 已启用")
        
        # off 子命令 - 禁用会话 TTS
        elif subcommand == "off":
            sid = self._sess_id(event)
            if self.global_enable:
                if sid not in self.disabled_sessions:
                    self.disabled_sessions.append(sid)
            else:
                if sid in self.enabled_sessions:
                    self.enabled_sessions.remove(sid)
            yield event.plain_result("❌ 本会话 TTS 已禁用")

        # globalon 子命令 - 全局启用
        elif subcommand == "globalon":
            if not event.is_admin():
                yield event.plain_result("🚫 权限不足，仅管理员可操作")
                return
            self.global_enable = True
            self._save_config()
            yield event.plain_result("✅ 全局 TTS 已启用 (黑名单模式)")

        # globaloff 子命令 - 全局禁用
        elif subcommand == "globaloff":
            if not event.is_admin():
                yield event.plain_result("🚫 权限不足，仅管理员可操作")
                return
            self.global_enable = False
            self._save_config()
            yield event.plain_result("❌ 全局 TTS 已禁用 (白名单模式)")
        
        # status 子命令 - 查看状态
        elif subcommand == "status":
            sid = self._sess_id(event)
            enabled = self._is_session_enabled(sid)
            mode = "黑名单模式（默认启用）" if self.global_enable else "白名单模式（默认禁用）"
            
            state = self._session_state.get(sid)
            last_tts = ""
            if state and state.last_tts_time > 0:
                elapsed = int(time.time() - state.last_tts_time)
                last_tts = f"\n最后 TTS: {elapsed}秒前"
            
            status = f"""📊 Genie TTS 状态

🔧 全局模式: {mode}
⚡ 当前会话: {'✅ 启用' if enabled else '❌ 禁用'}
🎲 触发概率: {self.prob}
📏 长度限制: {self.text_limit if self.text_limit > 0 else '无限制'}
⏰ 冷却时间: {self.cooldown}秒{last_tts}
🎙️ 服务器: {'✅ 就绪' if self.initialized else '❌ 未就绪'}"""
            
            yield event.plain_result(status)
        
        # 帮助信息
        else:
            help_msg = """📖 Genie TTS 命令帮助

» gentts test <文本> - 测试语音生成
» gentts on - 启用当前会话 TTS
» gentts off - 禁用当前会话 TTS
» gentts status - 查看 TTS 状态

--- 管理员指令 ---
» gentts globalon - 全局启用 TTS
» gentts globaloff - 全局禁用 TTS"""
            yield event.plain_result(help_msg)

    async def terminate(self):
        """插件卸载时清理临时文件"""
        try:
            if os.path.exists(self.temp_dir):
                for file in os.listdir(self.temp_dir):
                    file_path = os.path.join(self.temp_dir, file)
                    if os.path.isfile(file_path):
                        os.remove(file_path)
            logger.info("[GenieTTS] 插件已卸载，临时文件已清理")
        except Exception as e:
            logger.error(f"[GenieTTS] 清理失败: {e}")
