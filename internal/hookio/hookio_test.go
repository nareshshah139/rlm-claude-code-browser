package hookio

import (
	"bytes"
	"encoding/json"
	"os"
	"strings"
	"testing"
)

func TestReadInput(t *testing.T) {
	input := `{"session_id":"test-123","source":"startup","cwd":"/tmp"}`
	old := os.Stdin
	defer func() { os.Stdin = old }()
	os.Stdin = createTempFile(t, input)

	hi, err := ReadInput()
	if err != nil {
		t.Fatalf("ReadInput failed: %v", err)
	}
	if hi.SessionID != "test-123" {
		t.Errorf("SessionID = %q, want %q", hi.SessionID, "test-123")
	}
	if hi.Source != "startup" {
		t.Errorf("Source = %q, want %q", hi.Source, "startup")
	}
	if hi.CWD != "/tmp" {
		t.Errorf("CWD = %q, want %q", hi.CWD, "/tmp")
	}
}

func TestReadInputMalformed(t *testing.T) {
	old := os.Stdin
	defer func() { os.Stdin = old }()
	os.Stdin = createTempFile(t, "not json")

	_, err := ReadInput()
	if err == nil {
		t.Fatal("Expected error for malformed input")
	}
}

func TestPromptSubmitInput(t *testing.T) {
	var input HookInput
	if err := json.Unmarshal([]byte(`{"prompt":"compare the documents"}`), &input); err != nil {
		t.Fatal(err)
	}
	if input.UserPrompt != "compare the documents" {
		t.Fatalf("UserPrompt = %q", input.UserPrompt)
	}
}

func TestPromptContextOutput(t *testing.T) {
	old := os.Stdout
	r, w, _ := os.Pipe()
	defer r.Close()
	os.Stdout = w
	PromptContext("use bounded context tasks")
	w.Close()
	os.Stdout = old
	var parsed HookOutput
	if err := json.NewDecoder(r).Decode(&parsed); err != nil {
		t.Fatal(err)
	}
	if parsed.Decision != "" || parsed.Reason != "" || parsed.HookSpecific == nil {
		t.Fatal("Expected advisory hookSpecificOutput, not a decision")
	}
	if parsed.HookSpecific.HookEventName != "UserPromptSubmit" || parsed.HookSpecific.AdditionalContext != "use bounded context tasks" {
		t.Fatalf("Unexpected hook output: %+v", parsed.HookSpecific)
	}
}

func TestWriteOutput(t *testing.T) {
	old := os.Stdout
	r, w, _ := os.Pipe()
	os.Stdout = w

	output := &HookOutput{
		Decision: "approve",
		Reason:   "test reason",
	}
	err := WriteOutput(output)
	w.Close()
	os.Stdout = old

	if err != nil {
		t.Fatalf("WriteOutput failed: %v", err)
	}

	var buf bytes.Buffer
	buf.ReadFrom(r)

	var parsed HookOutput
	if err := json.Unmarshal(buf.Bytes(), &parsed); err != nil {
		t.Fatalf("Failed to parse output: %v", err)
	}
	if parsed.Decision != "approve" {
		t.Errorf("Decision = %q, want %q", parsed.Decision, "approve")
	}
}

func TestSessionContextOutput(t *testing.T) {
	old := os.Stdout
	r, w, _ := os.Pipe()
	os.Stdout = w

	SessionContext("test context")
	w.Close()
	os.Stdout = old

	var buf bytes.Buffer
	buf.ReadFrom(r)

	var parsed HookOutput
	if err := json.Unmarshal(buf.Bytes(), &parsed); err != nil {
		t.Fatalf("Failed to parse output: %v", err)
	}
	if parsed.HookSpecific == nil {
		t.Fatal("HookSpecific is nil")
	}
	if parsed.HookSpecific.HookEventName != "SessionStart" {
		t.Errorf("HookEventName = %q, want %q", parsed.HookSpecific.HookEventName, "SessionStart")
	}
	if parsed.HookSpecific.AdditionalContext != "test context" {
		t.Errorf("AdditionalContext = %q, want %q", parsed.HookSpecific.AdditionalContext, "test context")
	}
}

func TestPluginRootFromEnv(t *testing.T) {
	t.Setenv("CLAUDE_PLUGIN_ROOT", "/test/path")
	root := PluginRoot()
	if root != "/test/path" {
		t.Errorf("PluginRoot = %q, want %q", root, "/test/path")
	}
}

func TestProjectDirFromEnv(t *testing.T) {
	t.Setenv("CLAUDE_PROJECT_DIR", "/test/project")
	dir := ProjectDir()
	if dir != "/test/project" {
		t.Errorf("ProjectDir = %q, want %q", dir, "/test/project")
	}
}

func TestProjectDirFallback(t *testing.T) {
	t.Setenv("CLAUDE_PROJECT_DIR", "")
	dir := ProjectDir()
	if dir == "" {
		t.Error("ProjectDir should not be empty")
	}
}

func TestDebugNoOutput(t *testing.T) {
	t.Setenv("HOOK_DEBUG", "0")
	// Should not panic or produce output
	Debug("test %s", "message")
}

func TestHookInputToolFields(t *testing.T) {
	input := `{"session_id":"s","tool_name":"Read","tool_input":{"file_path":"/tmp/x"}}`
	old := os.Stdin
	defer func() { os.Stdin = old }()
	os.Stdin = createTempFile(t, input)

	hi, err := ReadInput()
	if err != nil {
		t.Fatalf("ReadInput failed: %v", err)
	}
	if hi.ToolName != "Read" {
		t.Errorf("ToolName = %q, want %q", hi.ToolName, "Read")
	}
	if hi.ToolInput == nil {
		t.Error("ToolInput should not be nil")
	}

	var ti map[string]string
	json.Unmarshal(hi.ToolInput, &ti)
	if ti["file_path"] != "/tmp/x" {
		t.Errorf("tool_input.file_path = %q, want %q", ti["file_path"], "/tmp/x")
	}
}

func createTempFile(t *testing.T, content string) *os.File {
	t.Helper()
	f, err := os.CreateTemp(t.TempDir(), "test-input-*.json")
	if err != nil {
		t.Fatal(err)
	}
	f.WriteString(content)
	f.Seek(0, 0)
	// Ensure content is readable
	if _, err := f.Stat(); err != nil {
		t.Fatal(err)
	}
	_ = strings.NewReader(content) // suppress unused import
	return f
}
