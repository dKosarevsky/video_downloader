from pathlib import Path

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
    if not title:
        return

    file_path = Path(file_name)
    extension = file_path.suffix.removeprefix('.') or DEFAULT_EXT

    with file_path.open('rb') as file:
        download_name = f'{title}.{extension}'
        if st.download_button('Download', data=file, file_name=download_name, mime=mime):
            with st.spinner('Downloading ...'):
                st.success('Download completed successfully.')
