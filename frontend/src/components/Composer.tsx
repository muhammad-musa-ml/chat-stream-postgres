import type { FormEvent, KeyboardEvent } from "react";

interface Props {
  value: string;
  streaming: boolean;
  error: string | null;
  onChange: (value: string) => void;
  onSend: () => void;
  onStop: () => void;
}

export function Composer({ value, streaming, error, onChange, onSend, onStop }: Props) {
  function submit(event: FormEvent) {
    event.preventDefault();
    onSend();
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    // Enter sends, Shift+Enter starts a new line.
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      onSend();
    }
  }

  return (
    <form className="composer" onSubmit={submit}>
      {error && (
        <p className="notice error" role="alert">
          Not sent: {error}
        </p>
      )}
      <div className="composer-row">
        <textarea
          aria-label="Message"
          placeholder="Type a message"
          rows={2}
          maxLength={8000}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={onKeyDown}
        />
        {streaming ? (
          <button type="button" className="stop" onClick={onStop}>
            Stop
          </button>
        ) : (
          <button type="submit" disabled={value.trim() === ""}>
            Send
          </button>
        )}
      </div>
    </form>
  );
}
