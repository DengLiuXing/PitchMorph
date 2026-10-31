from pathlib import Path

import numpy as np
import pytest

from app import stft

ROOT = Path(__file__).resolve().parent.parent
REF_SPEC = ROOT / "temp-dev" / "ref_spec.npy"
REF_LEAD = ROOT / "temp-dev" / "ref_lead.npy"


def signal(length: int = 44100) -> np.ndarray:
    rng = np.random.default_rng(0)
    return (rng.standard_normal(length) * 0.1).astype(np.float32)


def test_stft_shape():
    spec = stft.stft(signal())
    assert spec.shape == (101, stft.BINS)  # 1 s / 10 ms = 100 帧 + 1


def test_istft_round_trip():
    x = signal()
    back = stft.istft(stft.stft(x), len(x))
    # 端点有补零带来的边界效应，中间应当几乎无损
    assert np.abs(back[2048:-2048] - x[2048:-2048]).max() < 1e-4


def test_pack_features_layout():
    spec = np.zeros((3, stft.BINS, 2), dtype=complex)
    spec[0, 5, 1] = 1.0 + 2.0j
    packed = stft.pack_features(spec, zero_dc=False)
    base = (2 * 5 + 1) * 2
    assert packed[0, base] == pytest.approx(1.0)
    assert packed[0, base + 1] == pytest.approx(2.0)


def test_pack_features_zeroes_dc():
    spec = np.ones((2, stft.BINS, 2), dtype=complex)
    packed = stft.pack_features(spec, zero_dc=True)
    assert np.all(packed[:, 0:4] == 0.0)


def test_unpack_mask_layout():
    mask = np.zeros((3, 2050, 2), dtype=np.float32)
    mask[:, 2 * 5 + 1, 0] = 1.0
    mask[:, 2 * 5 + 1, 1] = -0.5
    out = stft.unpack_mask(mask)
    assert out[0, 5, 1] == pytest.approx(1.0 - 0.5j)


@pytest.mark.skipif(not REF_SPEC.exists(), reason="需要 temp-dev 里的 torch 黄金参考")
def test_matches_torch_reference():
    import soundfile as sf

    source = ROOT / "temp-dev/samples/openutau_official"
    wavs = sorted(source.glob("*.wav"))
    if not wavs:
        pytest.skip("没有样本")
    data, sr = sf.read(str(wavs[0]), dtype="float32")
    x = np.ascontiguousarray(data[: sr * 5].T)
    spec = np.stack([stft.stft(channel) for channel in x], axis=2)
    ref = np.load(REF_SPEC)
    assert spec.shape == ref.shape
    diff = np.abs(spec - ref).max() / np.abs(ref).max()
    assert diff < 1e-5, f"与 torch 的 STFT 相对误差 {diff}"


@pytest.mark.skipif(not REF_LEAD.exists(), reason="需要 temp-dev 里的 torch 黄金参考")
def test_istft_matches_torch_reference():
    import soundfile as sf

    source = ROOT / "temp-dev/samples/openutau_official"
    wavs = sorted(source.glob("*.wav"))
    if not wavs:
        pytest.skip("没有样本")
    data, sr = sf.read(str(wavs[0]), dtype="float32")
    x = np.ascontiguousarray(data[: sr * 5].T)
    spec = np.stack([stft.stft(channel) for channel in x], axis=2)
    masked = spec * (1.0 + 0.1j)
    lead = np.stack(
        [stft.istft(masked[:, :, index], x.shape[1]) for index in range(x.shape[0])], axis=0
    )
    ref = np.load(REF_LEAD).T
    diff = np.abs(lead - ref).max() / np.abs(ref).max()
    assert diff < 1e-5, f"与 torch 的 iSTFT 相对误差 {diff}"
