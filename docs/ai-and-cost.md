# AI steps and cost

Show which model handled each AI step, what it cost, and where your tokens and money go. Works with Trioditis and, read-only, with Claude Code's own transcripts.

[← Back to the README](../README.md)

---

If your AI helper talks to models through [Trioditis](https://github.com/alexb-joat/trioditis), Hlidskjalv shows which route and model handled each step, whether it ran locally (free, private) or in the cloud, the tokens used and, if you add prices, the cost:

```bash
python3 -m hlidskjalv watch ~/projects/my-app --trioditis-log ~/trioditis/logs/decisions.jsonl
```

For costs, copy `prices.example.json` to `~/.hlidskjalv/prices.json` and fill in each cloud model's price per million tokens from the provider's pricing page, or let Trioditis 0.4+ write it from a sourced price list: `python -m trioditis prices import provider-facts.json`. A model whose price rises with the prompt size carries a `long_context` tier, and Hlidskjalv prices it the way Trioditis does: above the threshold, the tier's prices apply to the whole call.

If you also use the [Claude Code](https://claude.com/claude-code) CLI in the same folder, its AI steps show up too — read-only, from its own session transcripts (`~/.claude/projects/...`), not from Trioditis. Model and token counts only, never prompt or response text. Since Claude Code runs on your subscription rather than per-token billing, those steps are labeled "subscription" instead of a dollar cost.

Each AI step names the program that sent it (Trioditis 0.4+ records it from the request's `User-Agent` or `X-Trioditis-Client` header), and a step no model answered says why: **over budget** (stop: raise the cap or add a local model) or **rate-limited · retry in N s** (wait: every model tried said "too many requests", and the program was told how long to wait).

The **Details** tab explains each AI step in full: Trioditis's reasons for the route and model, models it skipped (busy, down, rate-limited or over your spending cap), tokens in, cached and out, and cost. Its cost-efficiency panel totals spend per model and points at the cheapest next step, for example a route that keeps sending routine prompts to a paid model.

Details also shows, read-only:

- **Spending caps:** today's and this month's cloud spend against Trioditis's caps (from `~/.hlidskjalv/metrics.db` and `budget.json`), and cloud calls that had no price so the caps couldn't count them.
- **Claude Code usage:** per session in this project: turns, tokens in, how much of that was re-read earlier context, tokens out, the largest context of any one turn, and how many commands the AI ran. Sessions past a threshold you choose are flagged "long": starting fresh for a new task is the biggest saving on a plan.
- **By tool:** with Trioditis 0.4+, calls and cloud spend today and this month per program that sent them (for example a coding helper, a batch job, `curl`). The same numbers on the command line: `python -m trioditis usage --since month --by client`.
- **Providers:** per cloud provider, spend against its own cap, the rate limits it last reported (requests and tokens left, when they refill) and, where Trioditis has an account adapter (OpenRouter; Anthropic and OpenAI organizations with an admin key), your balance or this month's bill as the provider sees it.
- **Claude plan:** an estimate of your Claude subscription use in the same windows as the official meter (a 5-hour window that starts with your first message, and 7 days), split by model family. It's counted from Claude Code's transcripts on this computer, across all projects. It is not the official percentage: Anthropic doesn't publish how the caps are counted, and use in the claude.ai apps or on other machines shares the limits but leaves no transcripts here. You can enter the weekly reset time from the official panel once (Settings → Features) to anchor the week.
- **Local models for this computer:** Trioditis's hardware report (`~/.hlidskjalv/hardware.json`): how much memory a model may use, which installed models fit, and per route a suggestion with its reasons and the `config.yaml` change.

### Connect VS Code to Trioditis (Cline)

[Cline](https://marketplace.visualstudio.com/items?itemName=saoudrizwan.claude-dev) is a free, open-source AI coding helper for VS Code that asks before every edit and command. Point it at Trioditis (API Provider *OpenAI Compatible*, base URL `http://127.0.0.1:8787/v1`, model `route/code`) and every step it takes shows up here: route, model, tokens, cost, and which models were skipped and why. The full recipe, including the local-model context setting that keeps a small model from "forgetting" the task, is in [Trioditis's README](https://github.com/alexb-joat/trioditis/blob/main/docs/vscode-cline.md).

## The panel in the terminal

```bash
python3 -m hlidskjalv status
```

prints the Claude plan windows, Trioditis spend against its caps (and per provider), the last-reported rate limits, account balances, unread alerts and whether both services run: the same numbers as Details, without a browser. Run it from the project folder for that project's windows.
