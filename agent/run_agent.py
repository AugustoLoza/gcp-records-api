"""Self-correcting coding agent: plan -> act -> check -> iterate.

Given a spec and a failing test suite, this drives Claude through a bounded loop:
each turn it can write records_core.py and/or run the real pytest suite. The loop
never trusts Claude's own "looks good" — the only thing that ends it successfully
is run_tests() actually reporting PASSED. If it never gets there, we say so.
"""

import subprocess
import sys
from pathlib import Path

import anthropic

AGENT_DIR = Path(__file__).parent
IMPL_FILE = AGENT_DIR / "records_core.py"
TEST_FILE = AGENT_DIR / "test_records_core.py"
SPEC_FILE = AGENT_DIR / "spec.md"
MAX_ITERATIONS = 6

client = anthropic.Anthropic()


def run_tests() -> str:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(TEST_FILE), "-v", "--tb=short"],
        capture_output=True,
        text=True,
        cwd=AGENT_DIR,
        timeout=60,
    )
    status = "PASSED" if result.returncode == 0 else "FAILED"
    return f"{status}\n{result.stdout}\n{result.stderr}"


def write_file(content: str) -> str:
    IMPL_FILE.write_text(content)
    return f"Wrote {len(content)} bytes to {IMPL_FILE.name}"


def read_file() -> str:
    if not IMPL_FILE.exists():
        return "(file does not exist yet)"
    return IMPL_FILE.read_text()


TOOLS = [
    {
        "name": "write_file",
        "description": (
            f"Overwrite {IMPL_FILE.name} with the given content. "
            "This is the only file you are allowed to edit."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"content": {"type": "string", "description": "Full file content"}},
            "required": ["content"],
        },
    },
    {
        "name": "read_file",
        "description": f"Read the current content of {IMPL_FILE.name}.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "run_tests",
        "description": (
            f"Run the real pytest suite in {TEST_FILE.name} against your current "
            "implementation and return the actual output. This is the ONLY source of "
            "truth for whether your implementation is correct — trust this over your "
            "own judgment about the code."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
]


def execute_tool(name: str, tool_input: dict) -> str:
    if name == "write_file":
        return write_file(tool_input["content"])
    if name == "read_file":
        return read_file()
    if name == "run_tests":
        return run_tests()
    return f"Unknown tool: {name}"


def main() -> None:
    spec = SPEC_FILE.read_text()
    task = (
        "Implement records_core.py to satisfy the spec below and pass its test suite.\n\n"
        f"=== SPEC ===\n{spec}\n\n"
        "Work by writing the file, then running the tests, then reading the real output "
        "and fixing whatever is actually broken. Do not claim the task is done unless "
        "run_tests reports PASSED. When it does, stop."
    )
    messages: list = [{"role": "user", "content": task}]
    success = False
    iteration = 0

    for iteration in range(1, MAX_ITERATIONS + 1):
        print(f"\n{'=' * 20} Iteration {iteration}/{MAX_ITERATIONS} {'=' * 20}")

        response = client.messages.create(
            model="claude-opus-5",
            max_tokens=8000,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            tools=TOOLS,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})

        for block in response.content:
            if block.type == "text" and block.text.strip():
                print(f"[claude] {block.text.strip()}")

        if response.stop_reason != "tool_use":
            print("[harness] No further tool calls from Claude — verifying independently "
                  "rather than trusting that silence.")
            output = run_tests()
            print(output)
            success = output.startswith("PASSED")
            break

        tool_results = []
        iteration_passed = False
        for block in response.content:
            if block.type != "tool_use":
                continue
            print(f"[tool_use] {block.name}({block.input})")
            output = execute_tool(block.name, block.input)
            print(f"[tool_result] {output[:800]}")
            if block.name == "run_tests" and output.startswith("PASSED"):
                iteration_passed = True
            tool_results.append(
                {"type": "tool_result", "tool_use_id": block.id, "content": output}
            )

        messages.append({"role": "user", "content": tool_results})

        if iteration_passed:
            success = True
            break

    print(f"\n{'=' * 60}")
    if success:
        print(f"[harness] SUCCESS after {iteration} iteration(s) — tests pass.")
    else:
        print(f"[harness] FAILED after {MAX_ITERATIONS} iterations — reporting honestly, "
              "not claiming success. Last known state:")
        print(run_tests())


if __name__ == "__main__":
    main()
