# -*- coding: utf-8 -*-
"""C — hygien: `_parse_skill_md` måste klara YAML-frontmatter med fortsättning.

Bakgrund: parsern krävde att `description:` hade sitt värde på SAMMA rad.
Frontmatter är YAML, och YAML tillåter fortsättningsrader — både indenterade
och som block-scalar (`>` / `|`). Resultatet blev en TOM beskrivning, och
`_sync_from_filesystem` hoppade över filen.

28 av de installerade SKILL.md-filerna i `/usr/local/share/pi/skills` har
precis den formen. Det betyder att en fjärdedel av skills-biblioteket aldrig
synkades in — utan ett enda fel i loggen.
"""

import os

from odoo.tests.common import TransactionCase, tagged


@tagged('ai_core', 'post_install', '-at_install')
class TestParseSkillMd(TransactionCase):
    """18.4/18.5: fortsättningsrader ska ge en icke-tom beskrivning."""

    def _parse(self, text, fallback='fallback-name'):
        return self.env['ai.skill']._parse_skill_md(text, fallback)

    def test_single_line_description(self):
        text = (
            '---\n'
            'name: my-skill\n'
            'description: A short one.\n'
            '---\n'
            '# Body\n'
        )
        name, description, body = self._parse(text)
        self.assertEqual(name, 'my-skill')
        self.assertEqual(description, 'A short one.')
        self.assertIn('# Body', body)

    def test_description_continues_on_next_line(self):
        """Den vanligaste formen i skills-biblioteket."""
        text = (
            '---\n'
            'name: my-skill\n'
            'description: This is a long description that\n'
            '  continues on the next line.\n'
            '---\n'
            'Body text\n'
        )
        name, description, body = self._parse(text)
        self.assertEqual(name, 'my-skill')
        self.assertEqual(
            description,
            'This is a long description that continues on the next line.',
            'C: fortsättningsraden ska ingå i beskrivningen')

    def test_block_scalar_folded(self):
        text = (
            '---\n'
            'name: my-skill\n'
            'description: >\n'
            '  Folded text over\n'
            '  several lines.\n'
            '---\n'
            'Body\n'
        )
        _, description, _ = self._parse(text)
        self.assertEqual(description, 'Folded text over several lines.')

    def test_block_scalar_literal(self):
        text = (
            '---\n'
            'name: my-skill\n'
            'description: |\n'
            '  Literal text.\n'
            '---\n'
            'Body\n'
        )
        _, description, _ = self._parse(text)
        self.assertEqual(description, 'Literal text.')

    def test_description_after_other_keys(self):
        """Fortsättning får inte sluka nästa nyckel."""
        text = (
            '---\n'
            'name: my-skill\n'
            'description: First part\n'
            '  second part.\n'
            'license: MIT\n'
            '---\n'
            'Body\n'
        )
        name, description, _ = self._parse(text)
        self.assertEqual(name, 'my-skill')
        self.assertEqual(description, 'First part second part.')

    def test_missing_description_stays_empty(self):
        text = '---\nname: my-skill\n---\nBody\n'
        name, description, _ = self._parse(text)
        self.assertEqual(name, 'my-skill')
        self.assertEqual(description, '')

    def test_no_frontmatter_uses_fallback(self):
        name, description, body = self._parse('Just body text', 'dir-name')
        self.assertEqual(name, 'dir-name')
        self.assertEqual(description, '')
        self.assertEqual(body, 'Just body text')


@tagged('ai_core', 'post_install', '-at_install')
class TestRealSkillFiles(TransactionCase):
    """18.6: parsa de faktiska SKILL.md-filerna på disk."""

    SKILLS_DIR = '/usr/local/share/pi/skills'

    def test_installed_skills_get_descriptions(self):
        if not os.path.isdir(self.SKILLS_DIR):
            self.skipTest('skills-katalogen finns inte på denna maskin')

        Skill = self.env['ai.skill']
        total = 0
        empty = []
        for entry in sorted(os.listdir(self.SKILLS_DIR)):
            path = os.path.join(self.SKILLS_DIR, entry, 'SKILL.md')
            if not os.path.isfile(path):
                continue
            total += 1
            with open(path, encoding='utf-8') as fh:
                text = fh.read()
            _name, description, _body = Skill._parse_skill_md(text, entry)
            if not description:
                empty.append(entry)

        self.assertGreater(total, 0, 'inga SKILL.md-filer hittades')
        self.assertEqual(
            empty, [],
            'C: %d av %d SKILL.md-filer gav tom beskrivning — de hoppas '
            'över i _sync_from_filesystem: %s'
            % (len(empty), total, empty))
