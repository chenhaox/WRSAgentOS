"""Read-only M0 WRS probe. No device/controller or viewer is constructed."""


def probe_virtual():
    from importlib.metadata import version

    import wrs

    robot = wrs.xarm_lite6.Lite6()
    transforms = robot.fk()
    return {
        "import": "PASS",
        "dependencies": {
            name: version(name) for name in ("numpy", "scipy", "mujoco", "wgpu", "websockets")
        },
        "module": wrs.__file__,
        "robot_class": type(robot).__name__,
        "fk_shape": list(transforms.shape),
        "joints": robot.qs.tolist(),
        "hardware": False,
        "virtual_runtime_adapter": "UNIMPLEMENTED",
    }
