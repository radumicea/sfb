import argparse
import mimetypes
import os
import socket
import subprocess
from flask import Flask, Response, abort, render_template, send_file, request
from pathlib import Path
from urllib.parse import unquote


app = Flask(__name__)

BASE_DIR = '.'
BASE_DIR_WITH_SEP = BASE_DIR + '/'

LOCAL_IP = ''
PORT = ''


@app.route('/', defaults={'requested_path': ''})
@app.route('/<path:requested_path>', methods=['GET'])
def list_files(requested_path):
    requested_path = unquote(requested_path)
    relative_path = BASE_DIR_WITH_SEP + requested_path

    if relative_path.endswith('.thumbnail'):
        return send_thumbnail(relative_path)

    parent_path = Path(BASE_DIR).resolve()
    child_path = Path(relative_path).resolve()
    if parent_path != child_path and parent_path not in child_path.parents:
        abort(400, 'No such path')

    if os.path.isfile(relative_path):
        mime_type, _ = mimetypes.guess_type(relative_path)
        if mime_type and mime_type.startswith('video/'):
            return stream_video(relative_path, request.headers.get('Range', None), mime_type)
        else:
            return send_file(relative_path)

    if not os.path.isdir(relative_path):
        abort(400, 'No such path')

    file_list = []
    for entry in os.scandir(relative_path):
        if entry.is_dir():
            file_list.append({'name': entry.name, 'type': 'folder', 'path': f'{relative_path.lstrip(BASE_DIR_WITH_SEP)}/{entry.name}'.lstrip('/')})
        else:
            if entry.name.endswith('.thumbnail'):
                continue

            mime_type, _ = mimetypes.guess_type(entry.name)
            file_type = 'file'
            if mime_type:
                if mime_type.startswith('image/'):
                    file_type = 'image'
                elif mime_type.startswith('video/'):
                    file_type = 'video'
            file_list.append({'name': entry.name, 'type': file_type, 'path': f'{relative_path.lstrip(BASE_DIR_WITH_SEP)}/{entry.name}'.lstrip('/')})

    directory_name = os.path.basename(relative_path)
    if not directory_name:
        directory_name = 'root'

    return render_template(
        'directory.html',
        files=file_list,
        current_path=requested_path,
        directory_name=directory_name,
        endpoint=f'http://{LOCAL_IP}:{PORT}'
    )


def send_thumbnail(file_path):
    if os.path.isfile(file_path):
        return send_file(file_path)
    
    file_path = file_path.rstrip('.thumbnail')

    parent_path = Path(BASE_DIR).resolve()
    child_path = Path(file_path).resolve()
    if parent_path != child_path and parent_path not in child_path.parents:
        abort(400, 'No such path')
    
    if not os.path.isfile(file_path):
        abort(400, 'No such path')
        
    mime_type, _ = mimetypes.guess_type(file_path)
    if not mime_type.startswith('video/'):
        abort(400, 'Not a video')

    absolute_path = str(child_path)
    
    # Get middle of the video
    duration_command = [
        'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
        '-of', 'default=nokey=1:noprint_wrappers=1', absolute_path
    ]
    duration = float(subprocess.check_output(duration_command).strip())
    seek_time = duration * 0.5

    # Extract the middle frame
    thumbnail_path = absolute_path + '.thumbnail'

    ffmpeg_command = [
        'ffmpeg', '-ss', str(seek_time), '-i', absolute_path,
        '-vf', 'thumbnail', '-frames:v', '1', '-c:v', 'mjpeg', '-f', 'image2', thumbnail_path, '-y'
    ]
    subprocess.run(ffmpeg_command, check=True)

    return send_file(thumbnail_path)


def stream_video(file_path, range_header, mime_type):
    if not range_header:
        return send_file(file_path)

    file_size = os.path.getsize(file_path)
    byte_range = range_header.replace('bytes=', '').split('-')
    start = int(byte_range[0])
    end = int(byte_range[1]) if byte_range[1] else file_size - 1

    start_holder = [start]

    def generate():
        with open(file_path, 'rb') as f:
            f.seek(start_holder[0])
            while start_holder[0] <= end:
                chunk_size = 1024 * 1024  # 1MB
                data = f.read(min(chunk_size, end - start_holder[0] + 1))
                if not data:
                    break
                yield data
                start_holder[0] += len(data)

    response = Response(generate(), status=206, mimetype=mime_type)
    response.headers['Accept-Ranges'] = 'bytes'
    response.headers['Content-Range'] = f'bytes {start}-{end}/{file_size}'
    return response


if __name__ == '__main__':    
    parser = argparse.ArgumentParser(description='A simple HTTP server to serve files from a directory.')
    parser.add_argument(
        '--base-dir',
        type=str,
        default='.',
        help=(
            'The directory to serve files from. '
            'If not specified, the current working directory will be used.'
        )
    )
    parser.add_argument(
        '--port',
        type=int,
        default=-1,
        help=(
            'The port to run the server on. '
            'Specify a value between 0 and 65535. '
            'If not provided or invalid, a free port will be automatically selected.'
        )
    )
    args = parser.parse_args()

    BASE_DIR = args.base_dir
    BASE_DIR_WITH_SEP = BASE_DIR + '/'

    PORT = args.port
    if PORT < 0 or PORT > 65535:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('', 0))  # Bind to any free port
            PORT = s.getsockname()[1]  # Get the port number

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        # Use a dummy IP and port to determine local IP
        s.connect(("8.8.8.8", 80))
        LOCAL_IP = s.getsockname()[0]

    app.run(host='0.0.0.0', port=PORT)
