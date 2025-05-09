import streamlit as st

from urllib.error import URLError

from pytubefix import YouTube
from pytubefix.cli import on_progress
from pytubefix.exceptions import RegexMatchError, VideoUnavailable
from pendulum import Duration

from src.helpers.const import MIME, SAVE_PATH, DEFAULT_NAME
from src.helpers.utils import show_video, download_video_locally


def sort_resolutions(resolutions: list[str], reverse: bool = True) -> list[str]:
    return sorted(set(resolutions), key=lambda x: int(x[:-1]), reverse=reverse)


def po_token_verifier() -> tuple[str, str]:
    po_token = st.secrets.yt.po_token
    visitor_data = st.secrets.yt.visitor_data
    return visitor_data, po_token


def get_yt_obj(url: str) -> YouTube:
    try:
        return YouTube(
            url=url,
            use_po_token=True,
            allow_oauth_cache=False,
            po_token_verifier=po_token_verifier,
            on_progress_callback=on_progress,
        )
    except (URLError, RegexMatchError, VideoUnavailable) as err:
        st.error(body=err)
        st.stop()


def search_yt_resolution(yt_obj: YouTube, progressive: bool) -> list[str]:
    resolutions = [i.resolution for i in yt_obj.streams.filter(mime_type=MIME, progressive=progressive)]
    return sort_resolutions(resolutions)


def prepare_yt_video(yt_obj: YouTube, resolution: str, progressive: bool) -> str | None:
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
                        res=resolution,
                        progressive=progressive,
                    ).first().download(
                        output_path=SAVE_PATH,
                        filename=DEFAULT_NAME,
                    )
                    st.success('Video Prepared Successfully.')
                    return title


def download_yt_video(url: str):
    show_video(data=url)
    yt_obj = get_yt_obj(url=url)
    with st.spinner('Update Resolutions List ...'):
        c1, c2, _ = st.columns(3)
        progressive_res = c1.checkbox(label='Use Progressive Resolutions', value=True)
        resolutions = search_yt_resolution(yt_obj=yt_obj, progressive=progressive_res)
        resolution = c2.selectbox(label='Select Video Resolution:', options=resolutions or [])

    title = prepare_yt_video(yt_obj=yt_obj, resolution=resolution, progressive=progressive_res)
    if title:
        title = f'{title} {resolution}'
    download_video_locally(title=title)
