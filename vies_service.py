"""
vies_service.py - EU VIES SOAP VAT ID Verification Service (Task 9).

Validates a supplier's VAT ID against the official EU VIES SOAP service
(checkVat, countryCode + vatNumber, no API key required).
Rule: If the VIES service is unreachable, mark the check as 'unchecked',
never as 'invalid', and allow retrying later.
"""

import re
import logging
from datetime import datetime, timezone
from typing import Dict, Any

import requests
from lxml import etree

logger = logging.getLogger(__name__)

VIES_SOAP_URL = "https://ec.europa.eu/taxation_customs/vies/services/checkVatService"


def check_vat_vies(vat_id: str, timeout: float = 4.0) -> Dict[str, Any]:
    """
    Validate a VAT ID (e.g. 'DE287364519') against the EU VIES SOAP endpoint.
    Returns:
      {
        "status": "valid" | "invalid" | "unchecked",
        "checked_at": ISO-8601 timestamp,
        "country_code": "DE",
        "vat_number": "287364519",
        "trader_name": str,
        "trader_address": str,
        "details": str
      }
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    clean = re.sub(r"\s+", "", str(vat_id or "")).upper()

    if not clean or len(clean) < 4:
        return {
            "status": "unchecked",
            "checked_at": now_iso,
            "country_code": "",
            "vat_number": "",
            "trader_name": "",
            "trader_address": "",
            "details": "Keine USt-IdNr. angegeben.",
        }

    country_code = clean[:2]
    vat_number = clean[2:]

    # If format itself is not valid EU country + alphanumeric, mark invalid before network call
    if country_code == "DE" and not re.match(r"^\d{9}$", vat_number):
        return {
            "status": "invalid",
            "checked_at": now_iso,
            "country_code": country_code,
            "vat_number": vat_number,
            "trader_name": "",
            "trader_address": "",
            "details": "Formatfehler: Deutsche USt-IdNr. muss aus 'DE' und genau 9 Ziffern bestehen.",
        }

    soap_envelope = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                  xmlns:urn="urn:ec.europa.eu:taxud:vies:services:checkVat:types">
   <soapenv:Header/>
   <soapenv:Body>
      <urn:checkVat>
         <urn:countryCode>{country_code}</urn:countryCode>
         <urn:vatNumber>{vat_number}</urn:vatNumber>
      </urn:checkVat>
   </soapenv:Body>
</soapenv:Envelope>"""

    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": "",
    }

    try:
        resp = requests.post(VIES_SOAP_URL, data=soap_envelope.encode("utf-8"), headers=headers, timeout=timeout)
        if resp.status_code != 200:
            return {
                "status": "unchecked",
                "checked_at": now_iso,
                "country_code": country_code,
                "vat_number": vat_number,
                "trader_name": "",
                "trader_address": "",
                "details": f"EU VIES Dienst antwortete mit HTTP {resp.status_code} (nicht erreichbar, bitte später erneut prüfen).",
            }

        root = etree.fromstring(resp.content)
        fault = root.xpath("//*[local-name()='Fault']")
        if fault:
            return {
                "status": "unchecked",
                "checked_at": now_iso,
                "country_code": country_code,
                "vat_number": vat_number,
                "trader_name": "",
                "trader_address": "",
                "details": "EU VIES Dienst vorübergehend überlastet (SOAP Fault). Als ungeprüft markiert.",
            }

        valid_nodes = root.xpath("//*[local-name()='valid']/text()")
        name_nodes = root.xpath("//*[local-name()='name']/text()")
        addr_nodes = root.xpath("//*[local-name()='address']/text()")

        if not valid_nodes:
            return {
                "status": "unchecked",
                "checked_at": now_iso,
                "country_code": country_code,
                "vat_number": vat_number,
                "trader_name": "",
                "trader_address": "",
                "details": "Unerwartete VIES-Antwort; als ungeprüft markiert.",
            }

        is_valid = str(valid_nodes[0]).strip().lower() == "true"
        t_name = str(name_nodes[0]).strip() if name_nodes else ""
        t_addr = str(addr_nodes[0]).strip() if addr_nodes else ""

        return {
            "status": "valid" if is_valid else "invalid",
            "checked_at": now_iso,
            "country_code": country_code,
            "vat_number": vat_number,
            "trader_name": t_name,
            "trader_address": t_addr,
            "details": "VIES-Prüfung erfolgreich: USt-IdNr. gültig." if is_valid else "VIES-Prüfung: USt-IdNr. laut EU-Register nicht gültig.",
        }
    except Exception as exc:
        logger.info("VIES SOAP service unreachable (%s); marking VAT %s as unchecked.", exc, clean)
        return {
            "status": "unchecked",
            "checked_at": now_iso,
            "country_code": country_code,
            "vat_number": vat_number,
            "trader_name": "",
            "trader_address": "",
            "details": "EU VIES Dienst aktuell nicht erreichbar (Offline / Timeout). Als ungeprüft markiert.",
        }
