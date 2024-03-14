import os
import fnmatch


def generate_directory_tree(dir_path, prefix=""):
    """
    Generates a visual representation of the directory tree.
    """
    tree_str = ""
    items = os.listdir(dir_path)
    items_path = [os.path.join(dir_path, i) for i in items]
    # Sort items by directory, then by name
    items_sorted = sorted(items_path, key=lambda x: (not os.path.isdir(x), x))
    for i, item_path in enumerate(items_sorted, 1):
        item = os.path.basename(item_path)
        if os.path.isdir(item_path):
            connector = "├── " if i < len(items_sorted) else "└── "
            tree_str += f"{prefix}{connector}{item}\n"
            extension = "│   " if i < len(items_sorted) else "    "
            tree_str += generate_directory_tree(item_path, prefix=prefix + extension)
        else:
            connector = "├── " if i < len(items_sorted) else "└── "
            tree_str += f"{prefix}{connector}{item}\n"
    return tree_str


def traverse_and_copy(repo_path, pattern, output_file):
    directory_tree = generate_directory_tree(repo_path)
    with open(output_file, "w") as f_out:
        f_out.write("Project Structure:\n")
        f_out.write(directory_tree + "\n")
        for root, dirs, files in os.walk(repo_path):
            for filename in fnmatch.filter(files, pattern):
                file_path = os.path.join(root, filename)
                relative_path = os.path.relpath(file_path, start=repo_path)
                write_file_contents(f_out, relative_path, file_path)


def write_file_contents(f_out, relative_path, file_path):
    f_out.write(f"\nFile: {relative_path}\n")
    with open(file_path, "r") as f_in:
        contents = f_in.read()
    f_out.write(contents + "\n")
