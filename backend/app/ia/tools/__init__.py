"""Os módulos de tool. O registro que os expõe ao modelo mora em `app.ia.registro`.

Este arquivo é vazio de propósito: o registro precisa importar domínio (aprovação,
estoque), e o domínio precisa importar os módulos daqui. Com o registro no `__init__`,
importar `app.ia.tools.estoque` arrastava o domínio junto e fechava o ciclo.
"""
