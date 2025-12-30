import ssl
from functools import lru_cache
from pathlib import Path

import streamlit as st
from urllib.error import URLError

import certifi
from pytubefix import YouTube, extract, request
from pytubefix.botGuard import bot_guard
from pytubefix.cli import on_progress
from pytubefix.exceptions import BotDetection, RegexMatchError, VideoUnavailable, SABRError
from pytubefix.innertube import InnerTube
from pendulum import Duration
from moviepy import VideoFileClip, AudioFileClip
from proglog import ProgressBarLogger

from src.helpers.const import (
    MIME,
    SAVE_PATH,
    DEFAULT_NAME,
    DEFAULT_AUDIO_NAME,
    AUDIO_MIME,
    AUDIO_SOURCE_NAME,
)
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


def _apply_manual_po_token(yt_obj: YouTube, po_token_data: tuple[str, str]) -> None:
    """Inject visitorData and PoToken into the pytubefix instance without deprecated flags."""
    visitor_data, po_token = po_token_data
    if hasattr(yt_obj, '_visitor_data'):
        yt_obj._visitor_data = visitor_data
    if hasattr(yt_obj, '_pot'):
        yt_obj._pot = po_token
    yt_obj.po_token = po_token


def sort_results(results: list[str], reverse: bool = True, slice_range: int = 1) -> list[str]:
    return sorted(set(results), key=lambda x: int(x[:-slice_range]), reverse=reverse)


def get_yt_obj(url: str) -> YouTube:
    po_token_data: tuple[str, str] | None = None
    try:
        po_token_data = _prepare_po_token(url=url)
    except Exception as err:
        st.warning(f'Failed to auto-generate PoToken, continuing without it. Details: {err}')

    yt_client = 'WEB' if po_token_data else 'ANDROID_VR'

    try:
        yt_obj = YouTube(
            url=url,
            client=yt_client,
            allow_oauth_cache=False,
            on_progress_callback=on_progress,
        )
        if po_token_data:
            _apply_manual_po_token(yt_obj=yt_obj, po_token_data=po_token_data)
        return yt_obj
    except (URLError, RegexMatchError, VideoUnavailable, BotDetection) as err:
        st.error(body=err)
        st.stop()


def search_yt_resolution(yt_obj: YouTube, progressive: bool) -> list[str]:
    resolutions = [i.resolution for i in yt_obj.streams.filter(mime_type=MIME, progressive=progressive)]
    return sort_results(results=resolutions)


def search_bit_rates(yt_obj: YouTube) -> list[str]:
    po_token_present = bool(getattr(yt_obj, 'po_token', None))
    custom_filters = [lambda stream: not stream.is_sabr] if not po_token_present else None
    bit_rates = [
        i.abr
        for i in yt_obj.streams.filter(
            type='audio',
            custom_filter_functions=custom_filters,
        )
    ]
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


def _select_progressive_stream(yt_obj: YouTube):
    """Return the highest resolution progressive stream."""
    progressive_streams = yt_obj.streams.filter(
        progressive=True,
        file_extension=DEFAULT_NAME.split('.')[-1],
    ).fmt_streams

    if not progressive_streams:
        progressive_streams = yt_obj.streams.filter(progressive=True).fmt_streams

    if not progressive_streams:
        return None

    def _resolution_score(stream):
        if stream.resolution and stream.resolution.endswith('p'):
            value = ''.join(filter(str.isdigit, stream.resolution))
            if value.isdigit():
                return int(value)
        return 0

    return max(progressive_streams, key=_resolution_score)


def _extract_audio_from_video(video_path: str, audio_path: str = DEFAULT_AUDIO_NAME) -> bool:
    """Extract audio from a saved video file."""
    if not Path(video_path).exists():
        st.error('Temporary video file missing; unable to extract audio.')
        return False

    video_clip = None
    audio_clip = None
    try:
        video_clip = VideoFileClip(filename=video_path)
        audio_clip = video_clip.audio
        if not audio_clip:
            st.error('Unable to extract audio track from the video stream.')
            return False
        audio_clip.write_audiofile(audio_path)
        return True
    finally:
        if audio_clip:
            audio_clip.close()
        if video_clip:
            video_clip.close()


def _download_audio_via_progressive(yt_obj: YouTube) -> bool:
    """Fallback: download a progressive stream and extract audio."""
    stream = _select_progressive_stream(yt_obj=yt_obj)
    if not stream:
        st.error('No progressive stream available for audio extraction.')
        return False

    st.info('Extracting audio from a progressive stream. Provide PoToken for direct audio downloads.')
    stream.download(
        output_path=SAVE_PATH,
        filename=AUDIO_SOURCE_NAME,
    )
    success = _extract_audio_from_video(
        video_path=AUDIO_SOURCE_NAME,
        audio_path=DEFAULT_AUDIO_NAME,
    )
    Path(AUDIO_SOURCE_NAME).unlink(missing_ok=True)
    return success


def _download_audio_stream(yt_obj: YouTube, bit_rate: str | None) -> bool:
    """Download an audio stream with the selected bit rate."""
    if not bit_rate:
        return _download_audio_via_progressive(yt_obj=yt_obj)

    po_token_present = bool(getattr(yt_obj, 'po_token', None))

    custom_filters = []
    if not po_token_present:
        custom_filters.append(lambda stream: not stream.is_sabr)

    audio_query = yt_obj.streams.filter(
        type='audio',
        abr=bit_rate,
        custom_filter_functions=custom_filters or None,
    )

    stream = audio_query.first()
    if not stream and not po_token_present:
        return _download_audio_via_progressive(yt_obj=yt_obj)

    if not stream:
        st.error('Could not find an audio stream for the selected bit rate.')
        return False

    try:
        stream.download(
            output_path=SAVE_PATH,
            filename=DEFAULT_AUDIO_NAME,
        )
    except SABRError as err:
        if not po_token_present:
            return _download_audio_via_progressive(yt_obj=yt_obj)
        st.error('YouTube blocked this audio stream. Please provide a valid PoToken.')
        st.caption(f'Details: {err}')
        return False
    return True


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


def prepare_yt_media(
    yt_obj: YouTube,
    resolution: str | None,
    progressive: bool,
    bit_rate: str | None,
    audio_only: bool,
) -> tuple[str, str, str] | None:
    form_key = 'prepare_audio' if audio_only else 'prepare_yt_video'
    button_label = 'Prepare Audio' if audio_only else 'Prepare Video'
    spinner_label = 'Preparing Audio ...' if audio_only else 'Preparing Video ...'

    with st.form(form_key):
        if not st.form_submit_button(button_label):
            return None

        with st.spinner(spinner_label):
            if not yt_obj:
                return None

            title = yt_obj.title
            st.write(f'Title: `{title}`')
            st.write(f'Publish Date: `{yt_obj.publish_date}`')
            st.write(f'Duration: `{Duration(seconds=yt_obj.length)}`')
            st.write(f'Views: `{yt_obj.views}`')

            if audio_only:
                if not _download_audio_stream(yt_obj=yt_obj, bit_rate=bit_rate):
                    return None
                st.success('Audio Prepared Successfully.')
                return title, DEFAULT_AUDIO_NAME, AUDIO_MIME

            video_stream = yt_obj.streams.filter(
                type='video',
                res=resolution,
                progressive=progressive,
            ).first()

            if not video_stream:
                st.error('Could not find a video stream for the selected resolution.')
                return None

            video_stream.download(
                output_path=SAVE_PATH,
                filename=DEFAULT_NAME,
            )

            if not progressive:
                if not _download_audio_stream(yt_obj=yt_obj, bit_rate=bit_rate):
                    return None
                combine()

            st.success('Video Prepared Successfully.')
            return title, DEFAULT_NAME, MIME


def download_yt_video(url: str):
    show_video(data=url)
    yt_obj = get_yt_obj(url=url)
    download_mode = st.radio(
        'Download Type',
        options=('Video', 'Audio only'),
        horizontal=True,
    )
    audio_only = download_mode == 'Audio only'

    resolution = None
    bit_rate = None
    progressive_res = True

    with st.spinner('Update Options ...'):
        if audio_only:
            bit_rates = search_bit_rates(yt_obj=yt_obj)
            if bit_rates:
                bit_rate = st.selectbox(label='Select Audio Bit Rate:', options=bit_rates)
            else:
                st.info('No adaptive audio streams detected. Audio will be extracted from a progressive stream.')
        else:
            c1, c2, c3 = st.columns(3)
            progressive_res = c1.checkbox(label='Use Progressive Resolutions', value=True)
            resolutions = search_yt_resolution(yt_obj=yt_obj, progressive=progressive_res)
            resolution = c2.selectbox(label='Select Video Resolution:', options=resolutions or [])
            if not progressive_res:
                bit_rates = search_bit_rates(yt_obj=yt_obj)
                if bit_rates:
                    bit_rate = c3.selectbox(label='Select Audio Bit Rate:', options=bit_rates)
                else:
                    st.info('No adaptive audio streams detected. Audio will be extracted from a progressive stream.')

    result = prepare_yt_media(
        yt_obj=yt_obj,
        resolution=resolution,
        progressive=progressive_res,
        bit_rate=bit_rate,
        audio_only=audio_only,
    )

    if not result:
        return

    title, file_name, mime = result
    suffix = ''
    if not audio_only and resolution:
        suffix = f' {resolution}'
    elif audio_only and bit_rate:
        suffix = f' {bit_rate}'

    download_title = f'{title}{suffix}'
    download_video_locally(title=download_title, file_name=file_name, mime=mime)
