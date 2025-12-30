import ssl
from functools import lru_cache
from typing import Callable

import streamlit as st
from urllib.error import URLError

import certifi
from pytubefix import YouTube, extract, request
from pytubefix.botGuard import bot_guard
from pytubefix.cli import on_progress
from pytubefix.exceptions import BotDetection, RegexMatchError, VideoUnavailable
from pytubefix.innertube import InnerTube
from pendulum import Duration
from moviepy import VideoFileClip, AudioFileClip
from proglog import ProgressBarLogger

from src.helpers.const import MIME, SAVE_PATH, DEFAULT_NAME, DEFAULT_AUDIO_NAME
from src.helpers.utils import show_video, download_video_locally


def _configure_https_context() -> None:
    """Force urllib/pytubefix to trust certifi's CA bundle."""
    ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=certifi.where())


_configure_https_context()


def _read_secret_value(config, key: str) -> str | None:
    """Safely extract a value from Streamlit secrets (supports dict & attr access)."""
    if config is None:
        return None
    for accessor in ('get', '__getitem__'):
        try:
            if accessor == 'get' and hasattr(config, 'get'):
                value = config.get(key)
                if value:
                    return value
            elif accessor == '__getitem__':
                return config[key]
        except Exception:
            continue
    return getattr(config, key, None)


def _secret_po_token() -> tuple[str, str] | None:
    """Return visitor data + PoToken from secrets when available."""
    yt_secret = _read_secret_value(st.secrets, 'yt')
    if not yt_secret:
        yt_secret = getattr(st.secrets, 'yt', None)
    if not yt_secret:
        return None

    visitor_data = (
        _read_secret_value(yt_secret, 'visitor_data')
        or _read_secret_value(yt_secret, 'visitorData')
    )
    po_token = _read_secret_value(yt_secret, 'po_token')

    if visitor_data and po_token:
        return visitor_data, po_token
    return None


@lru_cache(maxsize=64)
def _resolve_visitor_data(video_id: str) -> str:
    """Mirror pytubefix logic for extracting visitorData."""
    try:
        watch_html = request.get(url=f'https://www.youtube.com/watch?v={video_id}')
        initial_data = extract.initial_data(watch_html)
        ctx = initial_data.get('responseContext')
        if ctx:
            return extract.visitor_data(str(ctx))
    except (RegexMatchError, URLError, KeyError):
        pass

    innertube_response = InnerTube('WEB').player(video_id)
    context = innertube_response.get('responseContext', {})
    visitor_data = context.get('visitorData')
    if visitor_data:
        return visitor_data

    for params in context.get('serviceTrackingParams', []):
        for param in params.get('params', []):
            if param.get('key') == 'visitor_data' and param.get('value'):
                return param['value']
    raise RuntimeError('Unable to resolve visitorData for PoToken generation.')


def _prepare_po_token(url: str) -> tuple[str, str] | None:
    """Return the visitorData/PoToken pair either from secrets or by generating it."""
    manual_tokens = _secret_po_token()
    if manual_tokens:
        return manual_tokens

    video_id = extract.video_id(url)
    visitor_data = _resolve_visitor_data(video_id)
    po_token = bot_guard.generate_po_token(video_id=video_id)
    return visitor_data, po_token


def sort_results(results: list[str], reverse: bool = True, slice_range: int = 1) -> list[str]:
    return sorted(set(results), key=lambda x: int(x[:-slice_range]), reverse=reverse)


def get_yt_obj(url: str) -> YouTube:
    po_token_data: tuple[str, str] | None = None
    try:
        po_token_data = _prepare_po_token(url=url)
    except Exception as err:
        st.warning(f'Failed to auto-generate PoToken, continuing without it. Details: {err}')

    po_token_verifier: Callable[[], tuple[str, str]] | None = (
        (lambda: po_token_data)
        if po_token_data
        else None
    )
    yt_client = 'WEB' if po_token_data else 'ANDROID_VR'

    try:
        return YouTube(
            url=url,
            client=yt_client,
            use_po_token=po_token_verifier is not None,
            allow_oauth_cache=False,
            po_token_verifier=po_token_verifier,
            on_progress_callback=on_progress,
        )
    except (URLError, RegexMatchError, VideoUnavailable, BotDetection) as err:
        st.error(body=err)
        st.stop()


def search_yt_resolution(yt_obj: YouTube, progressive: bool) -> list[str]:
    resolutions = [i.resolution for i in yt_obj.streams.filter(mime_type=MIME, progressive=progressive)]
    return sort_results(results=resolutions)


def search_bit_rates(yt_obj: YouTube) -> list[str]:
    bit_rates = [i.abr for i in yt_obj.streams.filter(type='audio')]
    return sort_results(results=bit_rates, slice_range=4)


class CustomBarLogger(ProgressBarLogger):

    def callback(self, **changes):
        # Every time the logger is updated, this function is called with
        # the `changes` dictionnary of the form `parameter: new value`.
        for (parameter, value) in changes.items():
            st.code(body=value)

    def bars_callback(self, bar, attr, value, old_value=None):
        # Every time the logger progress is updated, this function is called
        percentage = (value / self.bars[bar]['total']) * 100

        # my_bar = st.progress(value=0)
        if .0 <= value < 1.:
            st.progress(value=percentage)
        # my_bar.empty()


def combine() -> None:
    video_clip = VideoFileClip(filename=DEFAULT_NAME)
    audio_clip = AudioFileClip(filename=DEFAULT_AUDIO_NAME)

    # Заменяем аудиодорожку в видео на новую
    final_clip = video_clip.with_audio(audioclip=audio_clip)
    # video_clip.audio = audio_clip

    # Сохраняем результат
    final_clip.write_videofile(
        filename=DEFAULT_NAME,
        codec='libx264',  # кодек для видео
        audio_codec='aac',  # кодек для аудио
        logger=CustomBarLogger(),
    )


def prepare_yt_video(yt_obj: YouTube, resolution: str, progressive: bool, bit_rate: str | None = None) -> str | None:
    with st.form('prepare_yt_video'):
        if st.form_submit_button('Prepare Video'):
            with st.spinner('Preparing Video ...'):
                if yt_obj:
                    title = yt_obj.title
                    st.write(f'Title: `{title}`')
                    st.write(f'Publish Date: `{yt_obj.publish_date}`')
                    st.write(f'Duration: `{Duration(seconds=yt_obj.length)}`')
                    st.write(f'Views: `{yt_obj.views}`')
                    yt_obj.streams.filter(
                        type='video',
                        res=resolution,
                        progressive=progressive,
                    ).first().download(
                        output_path=SAVE_PATH,
                        filename=DEFAULT_NAME,
                    )
                    if not progressive:
                        yt_obj.streams.filter(
                            type='audio',
                            abr=bit_rate,
                        ).first().download(
                            output_path=SAVE_PATH,
                            filename=DEFAULT_AUDIO_NAME,
                        )
                        combine()
                    st.success('Video Prepared Successfully.')
                    return title


def download_yt_video(url: str):
    show_video(data=url)
    yt_obj = get_yt_obj(url=url)
    with st.spinner('Update Resolutions List ...'):
        c1, c2, c3 = st.columns(3)
        progressive_res = c1.checkbox(label='Use Progressive Resolutions', value=True)
        resolutions = search_yt_resolution(yt_obj=yt_obj, progressive=progressive_res)
        resolution = c2.selectbox(label='Select Video Resolution:', options=resolutions or [])

        bit_rate =None
        if not progressive_res:
            bit_rates = search_bit_rates(yt_obj=yt_obj)
            bit_rate = c3.selectbox(label='Select Audio Bit Rate:', options=bit_rates or [])

    title = prepare_yt_video(yt_obj=yt_obj, resolution=resolution, progressive=progressive_res, bit_rate=bit_rate)
    if title:
        title = f'{title} {resolution}'
    download_video_locally(title=title)
