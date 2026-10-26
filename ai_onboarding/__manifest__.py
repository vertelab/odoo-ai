# -*- coding: utf-8 -*-
{
    'name': 'AI: Assistant Onboarding',
    'version': '18.0.3.0.0',
    'summary': 'Onboardingskurs: co-pilot, coworker och AI i vardagen',
    'description': """
Lär dig använda AI-assistenten: fråga, delegera och granska — med människan i kontrollen.

5 sektioner, 13 slides (7 article, 1 infographic, 5 quiz). Sektion 5 är ett
manuellt test: fråga mot känd källa, delegera en åtgärd, och verifiera att ett
nej stoppar den.
""",
    'author': 'Vertel Sverige AB',
    'website': 'https://vertel.se',
    'license': 'LGPL-3',
    'category': 'Website/eLearning',
    'depends': ['website_slides'],
    'data': [
        'data/slide_channel.xml',
        'data/slide_slides_s1.xml',
        'data/slide_slides_s2.xml',
        'data/slide_slides_s3.xml',
        'data/slide_slides_s4.xml',
        'data/slide_slides_s5.xml',
    ],
    'installable': True,
    'auto_install': False,
    'application': False,
}
