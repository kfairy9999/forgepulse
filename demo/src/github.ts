/** Fetch PR files and contributor history from the GitHub API when a token is set. */

export interface GitHubEnrichment {
  paths: string[];
  author_merged_prs: number | null;
  fetched: boolean;
  error?: string;
}

export async function enrichFromGitHub(
  token: string,
  repo: string,
  prNumber: number,
  author: string,
): Promise<GitHubEnrichment> {
  const headers: Record<string, string> = {
    accept: "application/vnd.github+json",
    authorization: `Bearer ${token}`,
    "user-agent": "ForgePulse/0.2",
    "x-github-api-version": "2022-11-28",
  };
  try {
    const filesUrl = `https://api.github.com/repos/${repo}/pulls/${prNumber}/files?per_page=100`;
    const filesRes = await fetch(filesUrl, { headers });
    if (!filesRes.ok) {
      const text = await filesRes.text();
      return {
        paths: [],
        author_merged_prs: null,
        fetched: false,
        error: `GitHub files request failed (${filesRes.status}): ${text.slice(0, 180)}`,
      };
    }
    const files = (await filesRes.json()) as Array<{ filename?: string }>;
    const paths = files.map((f) => f.filename).filter((n): n is string => Boolean(n));

    let merged: number | null = null;
    if (author && author !== "unknown") {
      const q = encodeURIComponent(`repo:${repo} author:${author} is:pr is:merged`);
      const searchRes = await fetch(`https://api.github.com/search/issues?q=${q}&per_page=1`, { headers });
      if (searchRes.ok) {
        const body = (await searchRes.json()) as { total_count?: number };
        if (typeof body.total_count === "number") merged = body.total_count;
      }
    }
    return { paths, author_merged_prs: merged, fetched: true };
  } catch (error) {
    return {
      paths: [],
      author_merged_prs: null,
      fetched: false,
      error: error instanceof Error ? error.message : "GitHub request failed",
    };
  }
}
