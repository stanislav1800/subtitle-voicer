"""Output devices to choose from: full names instead of MME's cut ones, no sound mapper."""

from subvoicer.engines.audio.output import device_choices

HOSTAPIS = [{"name": "MME"}, {"name": "Windows DirectSound"}, {"name": "Windows WASAPI"}]


def out(name: str, hostapi: int) -> dict[str, object]:
    return {"name": name, "hostapi": hostapi, "max_output_channels": 2}


def test_full_names_and_no_sound_mapper() -> None:
    devices = [
        {"name": "Микрофон (Realtek(R) Audio)", "hostapi": 0, "max_output_channels": 0},
        out("Переназначение звуковых устр. - Output", 0),
        out("Динамики (Realtek(R) Audio)", 0),
        out("P275MR+ (NVIDIA High Definition", 0),
        out("Первичный звуковой драйвер", 1),
        out("P275MR+ (NVIDIA High Definition Audio)", 2),
    ]
    assert device_choices(devices, HOSTAPIS, 0) == [
        ("Динамики (Realtek(R) Audio)", "Динамики (Realtek(R) Audio)"),
        ("P275MR+ (NVIDIA High Definition", "P275MR+ (NVIDIA High Definition Audio)"),
    ]


def test_another_default_host_api_lists_every_device() -> None:
    devices = [out("Динамики (Realtek(R) Audio)", 2), out("Headphones - Output", 2)]
    assert [name for name, _ in device_choices(devices, HOSTAPIS, 2)] == [
        "Динамики (Realtek(R) Audio)",
        "Headphones - Output",
    ]
