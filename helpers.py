import os


############################################################################################################
# Helper functions
############################################################################################################

def resolve_file_path(file_name: str, base_dir=None, extension=None):
    """
    works out the path to a file based on the filename and optional base directory and can take an optional extension
    :param file_name: name of the file to resolve the path to
    :param base_dir: optional base directory to resolve the path from
    :param extension: optional extension to append to the file name
    :return: absolute path to the file or None
    """
    # return if file_name is None
    if file_name is None:
        return None

    # If base_dir is not specified, use the current working directory
    if base_dir is None:
        base_dir = os.getcwd()
    # If base_dir is a relative path, convert it to an absolute path based on the main.py directory
    elif not os.path.isabs(base_dir):
        main_dir = os.path.dirname(os.path.abspath(__file__))
        base_dir = os.path.abspath(os.path.join(main_dir, base_dir))
    # Expand user's home directory if base_dir starts with a tilde
    base_dir = os.path.expanduser(base_dir)

    # Check if base_dir exists and is a directory
    if not os.path.isdir(base_dir):
        return None

    # If the file_name is an absolute path, check if it exists
    file_name = os.path.expanduser(file_name)
    if os.path.isabs(file_name):
        if os.path.isfile(file_name):
            return file_name
        elif extension is not None and os.path.isfile(file_name + extension):
            return file_name + extension
    else:
        # If the file_name is a relative path, check if it exists
        full_path = os.path.join(base_dir, file_name)
        if os.path.isfile(full_path):
            return full_path
        elif extension is not None and os.path.isfile(full_path + extension):
            return full_path + extension

        # If the file_name is just a file name, check if it exists in the base directory
        full_path = os.path.join(base_dir, file_name)
        if os.path.isfile(full_path):
            return full_path
        elif extension is not None and os.path.isfile(full_path + extension):
            return full_path + extension

    # If none of the conditions are met, return None
    return None


def resolve_directory_path(dir_name: str):
    """
    works out the path to a directory
    :param dir_name: name of the directory to resolve the path to
    :return: absolute path to the directory
    """
    dir_name = os.path.expanduser(dir_name)
    if not os.path.isabs(dir_name):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), dir_name)
        if os.path.isdir(path):
            return path
    else:
        if os.path.isdir(dir_name):
            return dir_name
    return None


    # if len(url) > 0:
    #     # check so that -u will work alongside -f
    #     if 'load_file' not in session:
    #         session['load_file'] = []
    #     if 'load_file_name' not in session:
    #         session['load_file_name'] = []
    #     for u in url:
    #         # check prefix
    #         if not u.startswith('http') and not u.startswith('https'):
    #             u = 'https://' + u
    #         # make a request to URL
    #         response = requests.get(u)
    #         # parse the response
    #         soup = BeautifulSoup(response.text, 'html.parser')
    #         text = None
    #         if css_id is not None:
    #             text = soup.find(id=css_id).get_text()
    #         elif css_class is not None:
    #             text = soup.find(class_=css_class).get_text()
    #         # extract text less unnecessary newlines and whitespace
    #         if text is None:
    #             text = '\n'.join([line.strip() for line in soup.get_text().split('\n') if line.strip()])
    #         else:
    #             text = '\n'.join([line.strip() for line in text.split('\n') if line.strip()])
    #         session['load_file'].append(text)
    #         session['load_file_name'].append(u)