# -*- coding: utf-8 -*-
from __future__ import annotations
"""
Conference Loop — alla agenter svarar, bästa svar vinner
(change ai-orchestration-tidy-up, orchestration-code-unification 6.3 +
orchestration-spec-gaps 7.1/7.2/7.3).

Ersätter den inre ConferenceWrapper-klassen i _build_loop med en riktig
loop-klass som delar fan-out-logik med SupervisorLoop.

Mekanismer (ai.coworker.conference_mechanism):
  confidence (default) — varje agent svarar med JSON
                         {answer, confidence 0.0–1.0}; högst confidence vinner.
                         Fallback om parsning misslyckas: output_tokens-proxy.
  majority            — fas 1: alla svarar; fas 2: varje agent röstar på
                         bästa svar (index). Flest röster vinner; oavgjort
                         avgörs av confidence.
  synthesis           — router-LLM slår samman alla svar till ett.
"""

import asyncio
import json
import logging
from collections import Counter
from typing import Optional

from .provider import ChatResponse, Message, Role

_logger = logging.getLogger(__name__)


class ConferenceLoop:
    """Multi-agent conference with mechanism support."""

    def __init__(self, router_provider, agents, config=None,
                 mechanism: str = 'confidence'):
        self.router_provider = router_provider
        self.agents = {a.name: a for a in agents}
        self.config = config
        self.mechanism = mechanism or 'confidence'
        self.fan_out = getattr(config, 'fan_out_concurrency', 3)

        if not self.agents:
            raise ValueError("At least one specialist agent is required")

    # ── Public ──
    async def run(self, prompt: str,
                  history: Optional[list] = None) -> ChatResponse:
        """Run all agents on same question, select best answer."""
        if len(self.agents) == 1:
            agent = next(iter(self.agents.values()))
            return await agent.loop.run(prompt, history)

        results = await self._run_all(prompt, history)

        if self.mechanism == 'synthesis':
            return await self._synthesis(prompt, results)
        if self.mechanism == 'majority':
            return await self._majority(prompt, results, history)
        return self._by_confidence(results)

    # ── Fan-out ──
    async def _run_all(self, prompt, history):
        semaphore = asyncio.Semaphore(self.fan_out)

        async def run_agent(name, agent):
            async with semaphore:
                return name, await agent.loop.run(prompt, history)

        return await asyncio.gather(*[
            run_agent(name, a) for name, a in self.agents.items()
        ])

    # ── confidence (default) ──
    def _by_confidence(self, results):
        """Högst explicit confidence vinner; fallback output_tokens-proxy."""
        best_name, best_resp, best_conf = None, None, -1.0
        for name, resp in results:
            conf = self._extract_confidence(resp)
            if conf > best_conf:
                best_name, best_resp, best_conf = name, resp, conf
        text = self._clean_answer(best_resp.text)
        total_in = sum(r.input_tokens for _, r in results)
        total_out = sum(r.output_tokens for _, r in results)
        return ChatResponse(
            text=f"[Bästa svar från: {best_name}]\n\n{text}",
            input_tokens=total_in, output_tokens=total_out,
            finish_reason="stop",
        )

    # ── majority ──
    async def _majority(self, prompt, results, history):
        """Fas 1: svar. Fas 2: varje agent röstar på bästa svar (index)."""
        candidates = [
            (name, self._clean_answer(r.text)) for name, r in results
        ]
        candidate_block = "\n\n".join(
            f"[{i}] {name}: {text[:400]}"
            for i, (name, text) in enumerate(candidates)
        )
        semaphore = asyncio.Semaphore(self.fan_out)

        async def vote(name, agent):
            async with semaphore:
                vote_prompt = (
                    f"Candidate answers:\n{candidate_block}\n\n"
                    f"Which answer is best? Vote by index. "
                    f"Respond with JSON: {{\"vote\": <index>}}"
                )
                try:
                    resp = await agent.loop.run(vote_prompt, history)
                    data = json.loads(
                        self._extract_json(resp.text))
                    return name, int(data.get('vote', -1))
                except Exception:
                    return name, -1

        votes = dict(await asyncio.gather(*[
            vote(n, a) for n, a in self.agents.items()
        ]))
        counter = Counter(
            v for v in votes.values() if 0 <= v < len(candidates))
        if counter:
            winner_idx = counter.most_common(1)[0][0]
            winner_name, winner_text = candidates[winner_idx]
            votes_summary = ', '.join(
                f"{n}: {v if 0 <= v < len(candidates) else 'avstår'}"
                for n, v in votes.items())
        else:
            # Ingen giltig röst — fallback till confidence
            winner_idx = max(
                range(len(candidates)),
                key=lambda i: self._extract_confidence(results[i][1]))
            winner_name, winner_text = candidates[winner_idx]
            votes_summary = 'ingen giltig röst — confidence-fallback'
        total_in = sum(r.input_tokens for _, r in results)
        total_out = sum(r.output_tokens for _, r in results)
        return ChatResponse(
            text=f"[Majoritet: {winner_name} ({votes_summary})]\n\n{winner_text}",
            input_tokens=total_in, output_tokens=total_out,
            finish_reason="stop",
        )

    # ── synthesis ──
    async def _synthesis(self, prompt, results):
        """Slå samman alla svar med router-LLM."""
        agent_outputs = "\n\n".join(
            f"### {name}\n{self._clean_answer(resp.text)}"
            for name, resp in results)
        merge_prompt = (
            f"Original: {prompt}\n\nResponses:\n{agent_outputs}\n\n"
            f"Synthesize into ONE cohesive response.")
        try:
            from odoo.addons.ai_agent_core.core.provider import get_default_model_name
            model = (getattr(self.config, 'router_model', '')
                     or get_default_model_name() or 'cerebras/gpt-oss-120b')
            response = await self.router_provider.chat(
                model=model,
                messages=[Message(role=Role.USER, content=merge_prompt)],
                system_prompt="Merge answers.", temperature=0.3, max_tokens=2048,
            )
            total_in = sum(r.input_tokens for _, r in results) + response.input_tokens
            total_out = sum(r.output_tokens for _, r in results) + response.output_tokens
            return ChatResponse(text=response.text,
                                input_tokens=total_in, output_tokens=total_out,
                                finish_reason="stop")
        except Exception as e:
            _logger.warning("Conference synthesis failed: %s", e)
            return results[0][1]

    # ── Parsning ──
    @staticmethod
    def _extract_json(text):
        text = (text or "").strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        start, end = text.find('{'), text.rfind('}')
        return text[start:end + 1] if start >= 0 and end > start else '{}'

    def _extract_confidence(self, resp) -> float:
        """Explicit confidence 0–1 ur svaret; fallback output_tokens-proxy."""
        try:
            data = json.loads(self._extract_json(resp.text))
            conf = float(data.get('confidence', -1))
            if 0.0 <= conf <= 1.0:
                return conf
        except (ValueError, TypeError, json.JSONDecodeError):
            pass
        # Fallback: antal output-tokens som proxy (som tidigare)
        return (resp.output_tokens or 0) / 1000.0

    def _clean_answer(self, text):
        """Om agenten svarade med JSON {answer, confidence} → extrahera answer."""
        text = (text or '').strip()
        try:
            data = json.loads(self._extract_json(text))
            if isinstance(data, dict) and data.get('answer'):
                return data['answer']
        except (ValueError, json.JSONDecodeError):
            pass
        return text
