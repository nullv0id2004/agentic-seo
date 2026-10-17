/** Domain helpers mirroring rules/domains.py. */

// Sites that link to and rank for almost everything; they say nothing about a market. Mirrors GENERIC_DOMAINS.
export const GENERIC = ["youtube.com", "wikipedia.org", "reddit.com", "quora.com", "facebook.com", "instagram.com", "x.com", "twitter.com",
  "pinterest.com", "medium.com", "amazon.in", "amazon.com", "google.com", "linkedin.com", "apple.com", "microsoft.com"];

/** The registrable domain: tr.linkedin.com -> linkedin.com, jobs.glassdoor.co.in -> glassdoor.co.in. */
export function registrable(host: string): string {
  const labels = host.toLowerCase().replace(/^https?:\/\//, "").split("/")[0].replace(/^www\./, "").split(".").filter(Boolean);
  if (labels.length >= 3 && labels[labels.length - 1].length === 2 && ["co", "com", "org", "net", "gov", "ac", "edu"].includes(labels[labels.length - 2])) {
    return labels.slice(-3).join(".");
  }
  return labels.slice(-2).join(".");
}
