import streamlit as st

from urllib.error import URLError

from src.helpers.const import DEFAULT_NAME
from src.helpers.utils import show_video, download_video_locally
from src.rt import Rutube



@st.cache_data
def search_rt_resolution(video_url: str) -> list[str]:
    try:
        rt = Rutube(video_url)
        resolutions = [i.resolution for i in rt.playlist]
        return sorted(set(resolutions), reverse=True)
    except (URLError, ) as err:
        st.error(body=err)
        st.stop()


def prepare_rt_video(url: str, resolution: str):
    rt = Rutube(video_url=url)
    st.write(f'Title: `{rt._title}`')
    show_video(data=rt._m3u8_url, format_='application/x-mpegURL')
    with st.form('prepare_yt_video'):
        if st.form_submit_button('Prepare Video'):
            with st.spinner('Preparing Video ...'):
                video = [item for item in rt.playlist if item.resolution == resolution][0]
                video.download(filename=DEFAULT_NAME)
                title = video.title
                st.success(f'Video [`{title}`] Prepared Successfully.')
                return title


def download_rt_video(url: str):
    with st.spinner('Update Resolutions List ...'):
        c1, _,  _ = st.columns(3)
        resolutions = search_rt_resolution(video_url=url)
        resolution = c1.selectbox(label='Select Video Resolution:', options=resolutions or [])
    title = prepare_rt_video(url=url, resolution=resolution)
    download_video_locally(title=title)
