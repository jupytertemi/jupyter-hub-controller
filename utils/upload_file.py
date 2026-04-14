import os

from django.utils.text import get_valid_filename


class UploadFileHandler:
    def __init__(self, uploaded_file, path_folder):
        self.uploaded_file = uploaded_file
        self.path_folder = path_folder

    def save_file(self):
        """Upload file in folder ."""
        file_name = self.uploaded_file.name.lower()

        safe_filename = get_valid_filename(os.path.basename(file_name))
        upload_folder = self.path_folder
        os.makedirs(upload_folder, exist_ok=True)
        upload_path = os.path.join(upload_folder, safe_filename)

        with open(upload_path, "wb") as f:
            for chunk in self.uploaded_file.chunks():
                f.write(chunk)

        return safe_filename
