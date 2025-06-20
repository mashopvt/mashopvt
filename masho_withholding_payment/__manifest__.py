# -*- coding: utf-8 -*-
{
    'name': "Payment WHT",

    'summary': """
        Extend Payment usage with Withholding Taxes""",

    'description': """
        Extend Payment usage
    """,
    'author': "Mohsan Raza",
    'license':'AGPL-3',
    'website': "http://www.masho.co",
    'category': 'Studio',
    'version': '1.0',
    'module_type': 'official',
    'depends': ['account','base'],

    # always loaded
    'data': [
        'security/ir.model.access.csv',
        'views/account_payment_view.xml',
        'views/res_partner.xml',
        'wizard/account_payment_register_view.xml',
    ],
}
