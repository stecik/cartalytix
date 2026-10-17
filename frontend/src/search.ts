export const MIN_SUGGESTION_LENGTH = 3;

function fold(character: string): string {
  return character.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase();
}

export interface Segment {
  text: string;
  match: boolean;
}

// Mirrors the server's accent-insensitive matching so "rohlik" highlights "Rohlík".
export function highlight(text: string, term: string): Segment[] {
  const needle = [...term.trim()].map(fold).join("");
  const characters = [...text];
  const folded = characters.map(fold);
  const haystack = folded.join("");
  const start = needle ? haystack.indexOf(needle) : -1;
  if (start < 0) return [{ text, match: false }];
  let position = 0;
  let from = -1;
  let to = characters.length;
  for (const [index, value] of folded.entries()) {
    if (from < 0 && position >= start) from = index;
    position += value.length;
    if (position >= start + needle.length) {
      to = index + 1;
      break;
    }
  }
  return [
    { text: characters.slice(0, from).join(""), match: false },
    { text: characters.slice(from, to).join(""), match: true },
    { text: characters.slice(to).join(""), match: false },
  ].filter((segment) => segment.text !== "");
}
