# -*- coding: utf-8 -*-
"""Testhjälpare: skapa användare som faktiskt får finnas i databasen.

FYND 2026-11-11 (ledningssystem_copy, 102 fel i hela sviten):

    odoo.exceptions.ValidationError: Företag YourCompany finns inte bland
    de tillåtna företagen för användare HITL Godkännare ()

Samma fel låg bakom **~56 av 102 fel** — varje test som skapade en användare
föll. Klassen var inte trasig; anropet var ofullständigt:

    env['res.users'].create({'name': 'X', 'login': 'x'})

`res.users` har `_check_company` via `company_ids`. Utan `company_id` sätts
ingen standard, och utan `company_ids` är listan tom — då finns inget tillåtet
bolag och valideringen fäller anropet.

VARFÖR DET INTE SYNS I DRIFT: där råkade standardbolaget och användarens
bolag stämma. Ett test som förlitar sig på att miljön "råkar" ha rätt
standardbolag är skört — det faller i varje databas vars bolagsstruktur ser
annorlunda ut. Det är den sortens test som ser generiskt ut men inte är det.

Använd `create_user(env, ...)` i stället för att skriva `res.users.create`
för hand. Den sätter bolaget explicit, så testet mäter koden och inte
databasens förhistoria.
"""


def create_user(env, name, login, password='test-pass', groups=None,
                company=None, **extra):
    """Skapa en testanvändare med bolag satt — annars fäller `_check_company`.

    Args:
        env: Odoo-miljö
        name: visningsnamn
        login: unik inloggning
        password: lösenord (default 'test-pass')
        groups: xmlids att lägga användaren i (default: `base.group_user`)
        company: `res.company` att sätta som både `company_id` och enda
            tillåtna bolag. Default: `env.company`.
        **extra: övriga fält som skickas rakt igenom

    Returns:
        res.users

    VARFÖR `company_ids` OCKSÅ: `company_id` styr vilket bolag användaren
    arbetar i; `company_ids` är listan hen får se. Sätts bara det första är
    listan fortfarande tom och valideringen faller.
    """
    # SÄTT BOLAGET EXPLICIT.
    #
    # FYND 2026-11-12 (ledningssystem_copy): `res.users` har _check_company
    # via company_ids. Utan company_id sätts ingen standard, och utan
    # company_ids är listan tom — då fäller valideringen varje create med
    # "Företag X finns inte bland de tillåtna företagen". Samma fel låg
    # bakom ~56 av 102 fel i hela sviten.
    #
    # ROTORSAKEN i just den kopian var att admin (uid 2) hade
    # company_id=3 men company_ids={2,3,4}, medan env.company var
    # YourCompany (id 1). Miljön var inkonsekvent — men ett test ska inte
    # lita på att miljön är det. Vi tar env.company om den är tillåten,
    # annars det första tillåtna bolaget.
    if company is None:
        allowed = env.user.company_ids
        company = env.company if env.company in allowed else (
            allowed[:1] or env['res.company'].sudo().search([], limit=1))
    if not company:
        raise AssertionError(
            'Ingen tillåten company hittades — testmiljön saknar bolag')

    if groups is None:
        groups = ['base.group_user']
    vals = {
        'name': name,
        'login': login,
        'password': password,
        'company_id': company.id,
        'company_ids': [(6, 0, [company.id])],
        'groups_id': [(6, 0, [env.ref(g).id for g in groups])],
    }
    vals.update(extra)
    user = env['res.users'].create(vals)

    # SÄTT BOLAGET EN GÅNG TILL — efter create.
    #
    # Andra moduler hakar på `res.users.create` och kan skriva om
    # `company_ids` (project_customer_user, hr_holidays, website_slides har
    # alla egna `create`-overrides). `_check_company` är en @api.constrains
    # som körs EFTER create-kedjan, så en överskriven lista fäller anropet
    # med "Företag YourCompany finns inte bland de tillåtna företagen".
    #
    # Att sätta fältet igen efter create är idempotent och träffar den
    # slutliga raden — inte modulernas mellanläge.
    if user.company_id != company or user.company_ids != company:
        user.write({
            'company_id': company.id,
            'company_ids': [(6, 0, [company.id])],
        })
    return user
