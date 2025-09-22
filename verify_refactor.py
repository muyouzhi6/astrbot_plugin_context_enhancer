import ast
import traceback

def check_syntax(file_path):
    with open(file_path, 'r', encoding='utf-8') as file:
        source_code = file.read()
    try:
        ast.parse(source_code)
        print(f"文件 '{file_path}' 语法正确。")
        return True
    except SyntaxError as e:
        print(f"文件 '{file_path}' 存在语法错误:")
        traceback.print_exc()
        return False

if __name__ == "__main__":
    check_syntax("data/plugins/astrbot_plugin_context_enhancer/main.py")