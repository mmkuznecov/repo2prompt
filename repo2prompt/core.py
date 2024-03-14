import os
import fnmatch

def traverse_and_copy(repo_path, pattern, output_file):
    with open(output_file, 'w') as f_out:
        for root, dirs, files in os.walk(repo_path):
            for filename in fnmatch.filter(files, pattern):
                file_path = os.path.join(root, filename)
                write_file_contents(f_out, file_path, root)

def write_file_contents(f_out, file_path, root_path):
    relative_path = os.path.relpath(file_path, start=root_path)
    f_out.write(f"File: {relative_path}\n")
    with open(file_path, 'r') as f_in:
        contents = f_in.read()
    f_out.write(contents + "\n\n")