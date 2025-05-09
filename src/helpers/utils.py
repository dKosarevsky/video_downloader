import streamlit as st

from streamlit.runtime.media_file_storage import MediaFileStorageError
from src.helpers.const import MIME, DEFAULT_EXT, DEFAULT_NAME


def show_video(data, format_: str=MIME):
    try:
        st.video(data=data, format=format_)
    except MediaFileStorageError as err:
        st.error(body=err)
        st.stop()


def download_video_locally(title: str | None = None, file_name: str = DEFAULT_NAME, mime: str = MIME) -> None:
    if title:
        with open(file_name, 'rb') as file:
            if st.download_button('Download', data=file, file_name=f'{title}.{DEFAULT_EXT}', mime=mime):
                with st.spinner('Downloading Video ...'):
                    st.success('Video Downloaded Successfully.')
