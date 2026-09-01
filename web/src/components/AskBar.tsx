import { useState } from "react";
import type { CorpusCompany } from "../lib/stream";

interface Props {
  busy: boolean;
  companies: CorpusCompany[];
  onAsk: (question: string) => void;
  onStop: () => void;
}

export default function AskBar({ busy, companies, onAsk, onStop }: Props) {
  const [question, setQuestion] = useState("");

  const suggestions = companies.slice(0, 3).map((company) => {
    const period = company.periods[company.periods.length - 1] ?? "2025Q4";
    return `How did ${company.name} perform in ${period} and what is the main risk?`;
  });

  return (
    <form
      className="ask"
      onSubmit={(event) => {
        event.preventDefault();
        const trimmed = question.trim();
        if (trimmed.length >= 3 && !busy) onAsk(trimmed);
      }}
    >
      <div className="ask-row">
        <input
          className="ask-input"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="Ask about a company in the corpus, for example: How did Novaline Systems perform in 2025Q3?"
          aria-label="Question"
        />
        <button type="submit" className="button primary" disabled={busy || question.trim().length < 3}>
          Run
        </button>
        <button type="button" className="button" onClick={onStop} disabled={!busy}>
          Stop
        </button>
      </div>
      {!busy && suggestions.length > 0 && (
        <div className="ask-suggestions">
          {suggestions.map((text) => (
            <button
              type="button"
              key={text}
              className="chip"
              onClick={() => {
                setQuestion(text);
                onAsk(text);
              }}
            >
              {text}
            </button>
          ))}
        </div>
      )}
    </form>
  );
}
