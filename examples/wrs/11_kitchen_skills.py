"""按已注册技能播放默认厨具场景。

机械臂从关节零位开始。夹爪先张开，碗从把手上方落下再合拢，漏勺从侧面合拢。
食物倒进漏勺，漏勺放进煮面锅，停一会儿，拿起来抖几下，再把食物倒进空碗，
最后把漏勺放回架子。
"""

import argparse
import json

from wrs_agent.env.kitchen_motion import KitchenMotion
from wrs_agent.env.wrs import ROOT, VirtualModel
from wrs_agent.scene import load_scene

SCENE = ROOT / "wrs_agent/assets/kitchen/scene.toml"
SEQUENCE = (
    ("pick", {"object": "bowl_support_2"}),
    ("pour", {"source": "bowl_support_2", "target": "skimmer"}),
    ("place_on", {"object": "bowl_support_2", "surface": "bowl_rack_2"}),
    ("pick", {"object": "skimmer"}),
    ("seat_in", {"object": "skimmer", "container": "pot"}),
    ("pick", {"object": "skimmer"}),
    ("shake_off", {"object": "skimmer"}),
    ("pour", {"source": "skimmer", "target": "bowl_support"}),
    ("place_on", {"object": "skimmer", "surface": "skimmer_holder"}),
)


def hold_after(movie, skill, ticks):
    """Repeat the last frame of ``skill`` so the pot can sit before the next move."""
    held = []
    for index, frame in enumerate(movie):
        held.append(frame)
        following = movie[index + 1]["skill"] if index + 1 < len(movie) else None
        if frame["skill"] == skill and following != skill:
            held.extend([frame] * ticks)
    return held


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", type=SCENE.__class__, default=SCENE)
    parser.add_argument("--port", type=int, default=8008)
    parser.add_argument("--check", action="store_true", help="只规划并播放一遍，不打开页面")
    args = parser.parse_args()
    model = VirtualModel()
    model.load_scene(load_scene(args.scene))
    kitchen = KitchenMotion(model)
    # 关节零位。profile 里的 home 是展开姿态，不是回零。
    kitchen.model.apply([0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    print("正在按技能规划动作……", flush=True)
    movie = kitchen.record(SEQUENCE)
    foods = [f"bowl_food_{index}" for index in range(1, 5)]
    seen_pot = False
    for frame in movie:
        if frame["skill"] != "seat_in":
            continue
        kitchen.apply_capture(frame)
        if all(kitchen.collision.contains("pot", kitchen._pose(name)[0]) for name in foods):
            seen_pot = True
            break
    kitchen.apply_capture(movie[-1])
    in_bowl = [name for name in foods if kitchen.collision.contains("bowl_support", kitchen._pose(name)[0])]
    report = {
        "result": "PASS" if seen_pot and len(in_bowl) == 4 else "FAIL",
        "skills": [name for name, _ in SEQUENCE],
        "frames": len(movie),
        "food_in_pot": seen_pot,
        "food_in_bowl": in_bowl,
        "physics_enabled": False,
        "collision_checked": True,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    if args.check:
        return
    movie = hold_after(movie, "seat_in", int(2.5 * 20))
    from wrs_agent.env.viewer import viewer_hub

    kitchen.apply_capture(movie[0])
    # 大网格传给页面要几秒。若一进 run() 就播，页面出来时动作已经结束。
    # 这里先按回调次数停在第一帧，等页面能画出来再动，播完从头再来。
    rate = 20
    cursor = {"index": 0, "hold": int(6 * rate), "skill": None}
    world = model.wrs.wvw.World(
        cam_pos=(1.65, -1.85, 1.55),
        cam_lookat_pos=(0.3, 0, 0.23),
        port=args.port,
        hz=30,
        auto_start_hub=False,
    )
    for _, obj in model.objects.values():
        obj.add_to_scene(world.scene)
    model.robot.add_to_scene(world.scene)

    def update(_dt):
        if cursor["hold"] > 0:
            cursor["hold"] -= 1
            kitchen.apply_capture(movie[0])
            world.set_caption("FAFU 厨具技能 | 即将开始")
            return
        frame = movie[cursor["index"]]
        kitchen.apply_capture(frame)
        label = frame["skill"] or "开始"
        if label != cursor["skill"]:
            cursor["skill"] = label
            print(f"播放 {label}（{cursor['index'] + 1}/{len(movie)}）", flush=True)
        world.set_caption(f"FAFU 厨具技能 | {label} | {cursor['index'] + 1}/{len(movie)}")
        cursor["index"] += 1
        if cursor["index"] >= len(movie):
            cursor["index"] = 0
            cursor["hold"] = int(2 * rate)
            cursor["skill"] = None
            print("一轮结束，重新播放。", flush=True)

    world.schedule_interval(update, 1 / rate)
    try:
        with viewer_hub(args.port):
            print(
                f"打开 http://127.0.0.1:{args.port} 。"
                "页面出来后约 6 秒开始播放，播完会自动重来。Ctrl+C 退出。",
                flush=True,
            )
            world.run()
    finally:
        world.close()


if __name__ == "__main__":
    main()
