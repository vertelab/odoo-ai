# -*- coding: utf-8 -*-
"""Tester för bolagets förvalda coworker (autonomy-tab-and-company-default).

Default-coworkern är ett BOLAGSBESLUT (`res.company.ai_default_coworker_id`),
inte en egenskap hos en medarbetare. `_ai_default_coworker()` är den enda
uppslagningen; anroparen äger sin egen fallback.
"""

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCompanyDefaultCoworker(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.Coworker = cls.env['ai.coworker']
        cls.coworker = cls.Coworker.create({
            'name': 'Test — default-kandidat',
            'status': 'active',
        })

    def setUp(self):
        super().setUp()
        # Varje test börjar utan valt default — inget läcker mellan test.
        self.company.ai_default_coworker_id = False

    def test_tomt_falt_ger_none(self):
        """Utan valt default returneras None (ingen gissning)."""
        self.assertFalse(self.company._ai_default_coworker())

    def test_aktiv_coworker_returneras(self):
        """Bolagets val returneras när coworkern är aktiv."""
        self.company.ai_default_coworker_id = self.coworker
        self.assertEqual(
            self.company._ai_default_coworker(), self.coworker)

    def test_inaktiv_coworker_ger_none(self):
        """En avaktiverad coworker ska inte förväljas."""
        self.company.ai_default_coworker_id = self.coworker
        self.coworker.active = False
        self.assertFalse(self.company._ai_default_coworker())

    def test_valet_ar_per_bolag(self):
        """Två bolag kan peka på olika coworkers."""
        other = self.env['res.company'].create({'name': 'Test — andra bolaget'})
        second = self.Coworker.create({
            'name': 'Test — andra bolagets val',
            'status': 'active',
        })
        self.company.ai_default_coworker_id = self.coworker
        other.ai_default_coworker_id = second
        self.assertEqual(
            self.company._ai_default_coworker(), self.coworker)
        self.assertEqual(other._ai_default_coworker(), second)

    def test_is_default_finns_inte(self):
        """Flaggan på coworkern är borta — default bor på bolaget."""
        self.assertNotIn('is_default', self.Coworker._fields)

    def test_borttagen_coworker_ger_none(self):
        """Pekar valet på en RADERAD post ska uppslagningen ge None, inte krascha.

        `_ai_default_coworker()` läser `self.ai_default_coworker_id` — ett
        tomt recordset när målet raderats (ondelete sätter NULL) — och
        returnerar None. Det skiljer sig från avaktiverad (active=False),
        som testas separat, men båda ska ge None utan att kasta.
        """
        self.company.ai_default_coworker_id = self.coworker
        self.coworker.unlink()
        self.assertFalse(self.company._ai_default_coworker())


@tagged('post_install', '-at_install')
class TestEnsureDefaultCoworkerSeeding(TransactionCase):
    """Seedningen sätter bolagets default och en uttalad ordning.

    Bakgrunden: på en färsk installation sattes aldrig
    `res.company.ai_default_coworker_id`. `/ai/chat` föll då tillbaka på
    `web_ui_quests[0]`, sorterad på `sequence asc, name asc` — och en
    branschcoworker utan eget `sequence` (default 10) kunde sorteras före
    default-medarbetaren. Resultatet i drift: "3-vägsmatcharen" blev
    förvald i stället för "Allmän assistent".
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.Coworker = cls.env['ai.coworker']
        cls.default_coworker = cls.env.ref(
            'ai_agent_core.coworker_default_assistent',
            raise_if_not_found=False)

    def setUp(self):
        super().setUp()
        # Utgångsläge för varje test: inget valt default, så seedningen har
        # något att sätta.
        self.company.ai_default_coworker_id = False

    def _seed(self):
        """Kör adoptionen på samma sätt som data-XML:ens <function>."""
        return self.Coworker._ensure_default_coworker()

    def test_seedningen_satter_bolagets_default(self):
        """Tomt bolagsfält + körd seedning ⇒ default-medarbetaren.

        Detta är kärnan i fixen: färsk installation får en UTALAD default i
        stället för att förlita sig på att rätt post råkade bli först i en
        lista.
        """
        if not self.default_coworker:
            self.skipTest('default-medarbetaren saknas i testdatabasen')
        self.assertFalse(self.company.ai_default_coworker_id)
        self._seed()

        self.assertEqual(
            self.company.ai_default_coworker_id, self.default_coworker,
            'Seedningen ska sätta bolagets default-medarbetare när fältet '
            'är tomt — annars faller webbklienten tillbaka på "första '
            'web_ui-coworker", vilket kan vara en branschcoworker.',
        )

    def test_seedningen_skriver_inte_over_ett_valt_default(self):
        """Ett befintligt val lämnas orört, oavsett vilken coworker det pekar på.

        Defaulten är ett organisationsbeslut. Att peka på en branschcoworker
        är inte ett fel som seedningen ska "rätta".
        """
        if not self.default_coworker:
            self.skipTest('default-medarbetaren saknas i testdatabasen')
        chosen = self.Coworker.create({
            'name': 'Test — medvetet vald branschcoworker',
            'status': 'active',
        })
        self.company.ai_default_coworker_id = chosen

        self._seed()

        self.assertEqual(
            self.company.ai_default_coworker_id, chosen,
            'Seedningen får inte skriva över ett redan satt bolagsval.',
        )

    def test_seedningen_ar_idempotent(self):
        """Andra körningen ändrar ingenting.

        `<function>` körs vid BÅDE install och varje uppdatering, så en
        icke-idempotent skrivning skulle ändra `write_date` varje gång.
        """
        if not self.default_coworker:
            self.skipTest('default-medarbetaren saknas i testdatabasen')
        self._seed()
        after_first = self.company.ai_default_coworker_id

        self._seed()

        self.assertEqual(self.company.ai_default_coworker_id, after_first)
        self.assertEqual(after_first, self.default_coworker)

    def test_seedningen_satter_sequence_lagre_an_branschcoworker(self):
        """Default-medarbetaren står före en branschcoworker på sequence 10.

        Sekvensen låser inte defaulten (bolagsfältet gör det), men den tar
        bort den tysta tiebreak-vägen: `3-vägsmatcharen` får inte kunna bli
        förvald för att den saknar eget `sequence`.
        """
        if not self.default_coworker:
            self.skipTest('default-medarbetaren saknas i testdatabasen')
        industry = self.Coworker.create({
            # Namnet sorterar FÖRE "Allmän assistent", precis som
            # "3-vägsmatcharen" gör — så tiebreaket skulle välja den.
            'name': '0 Test — branschcoworker',
            'status': 'active',
        })
        self.assertEqual(industry.sequence, 10)
        # Tvinga default-medarbetaren tillbaka till modellens default, så
        # testet mäter seedningens säkring och inte data-XML:ens värde.
        self.default_coworker.sequence = 10

        self._seed()

        self.assertLess(
            self.default_coworker.sequence, industry.sequence,
            'Default-medarbetaren måste sortera före en branschcoworker som '
            'ligger kvar på modellens default-sequence.',
        )

    def test_seedningen_rör_inte_en_manuellt_satt_sequence(self):
        """En medvetet om-sorterad lista ska inte tvingas tillbaka.

        Säkringen skriver bara när värdet är kvar på modellens default (10).
        """
        if not self.default_coworker:
            self.skipTest('default-medarbetaren saknas i testdatabasen')
        self.default_coworker.sequence = 5

        self._seed()

        self.assertEqual(
            self.default_coworker.sequence, 5,
            'Seedningen får bara säkra sequence när värdet är kvar på 10.',
        )

    def test_seedningen_satter_sequence_fran_modellens_default(self):
        """sequence 10 ⇒ seedning ⇒ 1 (säkringen slår till)."""
        if not self.default_coworker:
            self.skipTest('default-medarbetaren saknas i testdatabasen')
        self.default_coworker.sequence = 10

        self._seed()

        self.assertEqual(self.default_coworker.sequence, 1)
