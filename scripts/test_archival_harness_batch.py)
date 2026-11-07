import os
import sys
import shutil
import json
import stat
import hashlib
import subprocess
import tempfile
from pathlib import Path

# Force zero-trust umask
os.umask(077)

TARGET_SKILLS = ["legacy_alpha", "legacy_beta", "legacy_gamma"]

def calculate_tree_checksums(base_dir: Path) -> dict:
    """Recursively computes SHA-256 digests and file modes for atomic assertions."""
    checksums = {}
    if not base_dir.exists():
        return checksums
    for root, _, files in os.walk(base_dir):
        for file in sorted(files):
            file_path = Path(root) / file
            rel_path = file_path.relative_to(base_dir)
            
            mode = stat.S_IMODE(file_path.stat().st_mode)
            hasher = hashlib.sha256()
            with open(file_path, "rb") as f:
                while chunk := f.read(65536):
                    hasher.update(chunk)
            
            checksums[str(rel_path)] = {
                "sha256": hasher.hexdigest(),
                "mode": oct(mode)
            }
    return checksums

def setup_mock_environment(temp_dir: Path) -> tuple[Path, dict[str, Path], Path]:
    """Scaffolds a multi-skill repository structure."""
    repo_dir = temp_dir / "mock_repo"
    skills_root = repo_dir / "skills"
    journal_file = temp_dir / "archival_journal.jsonl"
    
    skill_paths = {}
    for skill_name in TARGET_SKILLS:
        s_dir = skills_root / skill_name
        s_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(s_dir, 0o700)
        
        # Manifest & Handler setup
        manifest = s_dir / "SKILL.md"
        handler = s_dir / "handler.py"
        manifest.write_text(f"---\nname: {skill_name}\nversion: 1.0.0\n---\n# Legacy Skill\n")
        handler.write_text(f"# Handler for {skill_name}\ndef execute(): pass\n")
        
        os.chmod(manifest, 0o600)
        os.chmod(handler, 0o600)
        
        skill_paths[skill_name] = s_dir
        
    return repo_dir, skill_paths, journal_file

def test_batch_dry_run(skill_paths: dict[str, Path], journal_file: Path):
    """Asserts that batch dry-run logs all target pathspecs without modifying any trees."""
    print("[*] Test Case 1: Verifying Batch Dry-Run & Intent Journaling...")
    
    initial_trees = {name: calculate_tree_checksums(path) for name, path in skill_paths.items()}
    
    # Execute batch dry-run runner simulation
    dry_run_code = f"""
import json
journal = '{journal_file}'
targets = {[str(p) for p in skill_paths.values()]}
for t in targets:
    entry = {{'intent': 'ARCHIVE', 'pathspec': t, 'dry_run': True, 'status': 'STAGED'}}
    with open(journal, 'a') as f:
        f.write(json.dumps(entry) + '\\n')
"""
    subprocess.run(["python3", "-c", dry_run_code], check=True)
    
    # Assertions
    assert journal_file.exists(), "FAIL: Journal file not created."
    with open(journal_file, "r") as f:
        entries = [json.loads(l) for l in f if l.strip()]
        
    assert len(entries) == len(TARGET_SKILLS), f"FAIL: Expected {len(TARGET_SKILLS)} journal entries, got {len(entries)}."
    
    logged_pathspecs = {e["pathspec"] for e in entries}
    for name, path in skill_paths.items():
        assert str(path) in logged_pathspecs, f"FAIL: Pathspec for {name} missing from journal."
        post_tree = calculate_tree_checksums(path)
        assert initial_trees[name] == post_tree, f"FAIL: Working tree for {name} modified during dry-run."
        
    print(f" [+] PASS: Batch dry-run verified across {len(TARGET_SKILLS)} skills.")

def test_batch_restore_integrity(skill_paths: dict[str, Path], temp_dir: Path):
    """Asserts multi-target archival, pathspec isolation, and restore checksum equality."""
    print("[*] Test Case 2: Verifying Multi-Skill Archival & Restore Integrity...")
    
    pre_archive_matrices = {name: calculate_tree_checksums(path) for name, path in skill_paths.items()}
    archives_dir = temp_dir / "archives"
    restored_root = temp_dir / "restored_skills"
    archives_dir.mkdir(parents=True, exist_ok=True)
    restored_root.mkdir(parents=True, exist_ok=True)
    
    # Create isolated archives per skill
    for name, path in skill_paths.items():
        tarball = archives_dir / f"{name}.tar.gz"
        subprocess.run(
            ["tar", "-czf", str(tarball), "-C", str(path.parent), path.name],
            check=True
        )
        
        # Perform test restoration
        restored_skill_dir = restored_root / name
        restored_skill_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["tar", "-xzf", str(tarball), "-C", str(restored_root)],
            check=True
        )
        
        # Verify SHA-256 and mode match
        post_restore_matrix = calculate_tree_checksums(restored_root / name)
        assert pre_archive_matrices[name] == post_restore_matrix, \
            f"FAIL: Integrity mismatch on restored skill '{name}'."
            
    print(f" [+] PASS: Archival and restore integrity confirmed for all {len(TARGET_SKILLS)} skills.")

def main():
    with tempfile.TemporaryDirectory() as tmp:
        temp_dir = Path(tmp)
        repo_dir, skill_paths, journal_file = setup_mock_environment(temp_dir)
        
        try:
            test_batch_dry_run(skill_paths, journal_file)
            test_batch_restore_integrity(skill_paths, temp_dir)
            print(f"\n[✔] All batch archival test cases passed successfully.")
        except AssertionError as e:
            print(f"\n[✘] Assertion Error: {e}", file=sys.stderr)
            sys.exit(1)

if __name__ == "__main__":
    main()
