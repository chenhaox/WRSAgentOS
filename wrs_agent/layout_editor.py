"""Terminal editing of the current disposable kitchen; defaults are never written."""

import argparse
import json
import math
import os
import re
import tempfile
import tomllib
from pathlib import Path

from wrs_agent.layout_session import SESSION_DIRECTORY, current_session
from wrs_agent.scene import SceneConfig, load_scene
from wrs_agent.scene_layout import LayoutConfig, Placement, apply_layout
from wrs_agent.schemas import MAX_BYTES, encode


def editor_state(scene):
    objects = load_scene(scene)
    config = SceneConfig.model_validate(tomllib.loads(Path(scene).read_text(encoding="utf-8")))
    if config.layout is None:
        raise ValueError("当前 scene.toml 没有关联 layout 文件，不能使用摆放编辑器。")
    path = (Path(scene).resolve().parent / config.layout).resolve()
    if not path.is_relative_to(Path(scene).resolve().parent):
        raise ValueError("layout 文件必须位于场景目录内。")
    contents = path.read_bytes()
    if len(contents) > MAX_BYTES:
        raise ValueError("layout_config_too_large")
    layout = LayoutConfig.model_validate(tomllib.loads(contents.decode("utf-8")))
    return objects, path, layout.objects


def parse_vector(text):
    values = [float(value) for value in text.replace("，", ",").replace(",", " ").split()]
    if len(values) != 3 or not all(math.isfinite(value) for value in values):
        raise ValueError("请输入三个有限数字，例如：0.30, -0.20, 0.05；不要填写单位。")
    return values


def select_object(text, objects):
    names = list(objects)
    if text.isdecimal() and 1 <= int(text) <= len(names):
        return names[int(text) - 1]
    exact = [name for name, obj in objects.items()
             if text.casefold() in (name.casefold(), obj.label.casefold())]
    if len(exact) == 1:
        return exact[0]
    raise ValueError("没有找到唯一的厨具，请输入列表中的编号、完整中文名称或物体 ID。")


def replace_pose(text, name, pos, rpy_deg):
    # This editor targets the shipped [objects.ID] template, not arbitrary TOML formatting.
    header = re.search(rf"(?m)^[ \t]*\[objects\.{re.escape(name)}\][ \t]*(?:#[^\r\n]*)?\r?$", text)
    if header is None:
        raise ValueError(f"找不到 [objects.{name}]；请保持模板中的标准表头格式。")
    start = header.end()
    following = re.search(r"(?m)^[ \t]*\[", text[start:])
    end = start + following.start() if following else len(text)
    block = text[start:end]
    newline = "\r\n" if "\r\n" in text else "\n"
    for key, value in (("pos", pos), ("rpy_deg", rpy_deg)):
        pattern = rf"(?m)^([ \t]*{key}[ \t]*=[ \t]*)\[[^\]]*\]([ \t]*(?:#[^\r\n]*)?)(\r?)$"
        replacement = json.dumps(value, allow_nan=False)
        block, count = re.subn(
            pattern, lambda match, value=replacement: match[1] + value + match[2] + match[3],
            block,
        )
        if count == 0:
            if key == "pos":
                raise ValueError("没有找到标准 pos 数组，未修改文件。")
            block = block.rstrip("\r\n") + newline + f"{key} = {replacement}" + newline * 2
        elif count != 1:
            raise ValueError(f"重复的 {key} 字段，未修改文件。")
    return text[:start] + block + text[end:]


def save_pose(scene, name, pos, rpy_deg, *, session_directory=None):
    scene = Path(scene).resolve()
    if session_directory is not None and current_session(session_directory) != scene:
        raise ValueError("05 已重新启动，本次输入已取消；请重新选择厨具。")
    scene_before = scene.read_bytes()
    objects, path, placements = editor_state(scene)
    if name not in placements:
        raise ValueError("物体已经从配置中移除，请重新选择。")
    current = placements[name]
    checked = Placement.model_validate({
        "pos": pos, "rpy_deg": rpy_deg, "parent": current.parent,
    })
    before = path.read_bytes()
    text = replace_pose(before.decode("utf-8"), name, checked.pos, checked.rpy_deg)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent,
                                         prefix=".layout-", suffix=".tmp", delete=False) as file:
            temp_path = Path(file.name)
            file.write(text.encode("utf-8"))
            file.flush()
            os.fsync(file.fileno())
        resolved = apply_layout(temp_path, objects)
        if len(encode(SceneConfig(objects=resolved))) > MAX_BYTES // 2:
            raise ValueError("scene_snapshot_too_large")
        if path.read_bytes() != before or scene.read_bytes() != scene_before:
            raise ValueError("配置刚被其他程序修改，本次未覆盖。请重新选择并输入。")
        if session_directory is not None and current_session(session_directory) != scene:
            raise ValueError("05 已重新启动，本次输入已取消；请重新选择厨具。")
        os.replace(temp_path, path)
        return resolved[name]
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def ask_vector(prompt, current):
    while True:
        text = input(f"{prompt}（回车保留，q 取消本次修改）：").strip()
        if text.lower() == "q":
            return None
        if not text:
            return current
        try:
            return parse_vector(text)
        except ValueError as error:
            print(f"输入无效：{error}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="临时修改厨具摆放，重启 05 后恢复默认")
    parser.add_argument("--session-dir", type=Path, default=SESSION_DIRECTORY,
                        help="运行副本目录（通常无需修改）")
    parser.add_argument("--list", action="store_true", help="只列出物体，不修改文件")
    args = parser.parse_args(argv)
    print("厨具摆放编辑器：位置用米，角度用度；不连接真实机械臂。")
    print("本次修改仅作用于运行副本；默认配置不变，重启 05 后恢复默认。")
    try:
        while True:
            scene = current_session(args.session_dir)
            objects, path, placements = editor_state(scene)
            print(f"\n本次运行副本：{path}")
            for index, (name, obj) in enumerate(objects.items(), 1):
                print(f"{index:2}. {obj.label or name}  [{name}]")
            if args.list:
                return 0
            text = input("输入编号 / 完整厨具名称 / 物体 ID（q 退出）：").strip()
            if text.lower() == "q":
                return 0
            try:
                name = select_object(text, objects)
                placement = placements[name]
                frame = ("世界坐标" if placement.parent is None else
                         f"相对于 {objects[placement.parent].label} [{placement.parent}]")
                print(f"\n正在编辑：{objects[name].label} [{name}]；{frame}")
                print(f"当前配置位置：{placement.pos} 米；角度：{placement.rpy_deg} 度")
                print(f"当前世界位置：{objects[name].pos}")
                if placement.parent:
                    print("若要整套一起移动，请编辑其父物体；这里输入的是相对父物体的值。")
                pos = ask_vector("新位置 X(红), Y(绿), Z(蓝)", placement.pos)
                if pos is None:
                    continue
                rpy = ask_vector("新角度 绕X(红), 绕Y(绿), 绕Z(蓝)", placement.rpy_deg)
                if rpy is None:
                    continue
                if pos == placement.pos and rpy == placement.rpy_deg:
                    print("没有变化，未写入文件。")
                    continue
                result = save_pose(scene, name, pos, rpy, session_directory=args.session_dir)
                print(f"已应用到本次场景；新的世界位置：{result.pos}")
                print("05 节点空闲时约 1 秒内自动读取；刷新 http://127.0.0.1:8000 查看。")
            except (OSError, ValueError) as error:
                print(f"未保存：{error}")
    except (EOFError, KeyboardInterrupt):
        print("\n已退出；尚未保存的输入不会写入文件。")
        return 0
    except (OSError, ValueError) as error:
        print(f"无法打开配置：{error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
