import os
import time

from backend import tempsweep


def unpacked(root, name, frontier=True, age=3600):
    folder = root/name
    (folder/'dist'/'assets').mkdir(parents=True)
    if frontier:
        (folder/'dist'/'index.html').write_text('<html>', encoding='utf-8')
        (folder/'dist'/'assets'/'DesktopWorkspace-abc.js').write_text('', encoding='utf-8')
    old = time.time() - age
    os.utime(folder, (old, old))
    return folder


def test_stale_frontier_copies_are_removed(tmp_path):
    stale = unpacked(tmp_path, '_MEI00001')
    assert tempsweep.sweep(tmp_path, own='') == 1
    assert not stale.exists() and not stale.with_name(stale.name + '.stale').exists()


def test_other_programs_copies_stay(tmp_path):
    other = unpacked(tmp_path, '_MEI00002', frontier=False)
    assert tempsweep.sweep(tmp_path, own='') == 0
    assert other.exists()


def test_young_copies_and_the_running_one_stay(tmp_path):
    young = unpacked(tmp_path, '_MEI00003', age=5)
    mine = unpacked(tmp_path, '_MEI00004')
    assert tempsweep.sweep(tmp_path, own=str(mine)) == 0
    assert young.exists() and mine.exists()


def test_unrelated_folders_stay(tmp_path):
    keep = unpacked(tmp_path, 'not_a_mei_folder')
    assert tempsweep.sweep(tmp_path, own='') == 0
    assert keep.exists()


def test_only_the_packaged_backend_sweeps():
    assert tempsweep.start() is None  # Tests run from source, where nothing is unpacked.
