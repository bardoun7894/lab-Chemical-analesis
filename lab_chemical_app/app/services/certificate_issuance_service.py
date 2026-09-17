"""Validation and specification facts used while issuing certificates.

This module deliberately owns no numbering or writes.  It converts an order's
Application/product configuration into the claims a certificate may make, and
compares those claims before the route allocates a certificate number.
"""

from dataclasses import dataclass

from app.services.application_spec_service import standard_labels


LEGACY_APPLICATION_STANDARDS = ("ISO 2531", "EN 545")


def _parameter_name(parameter):
    if parameter is None:
        return ""
    return (getattr(parameter, "name_en", None)
            or getattr(parameter, "code", None)
            or "").strip()


def _normalise(value):
    return " ".join((value or "").split()).casefold()


def _zinc_value(parameter):
    name = _parameter_name(parameter)
    normalized = _normalise(name).replace('-', ' ').replace('_', ' ')
    absent_markers = ('no zinc', 'without zinc', 'non zinc', 'none', 'بدون زنك')
    if not name or any(marker in normalized for marker in absent_markers):
        return False, ''
    return True, name


@dataclass(frozen=True)
class CertificateSpecification:
    application_standards: tuple
    zinc_present: bool
    zinc_type: str
    internal_finish: str
    external_finish: str
    used_application_fallback: bool = False

    def compatibility_values(self):
        return {
            "Application standards": tuple(
                _normalise(value) for value in self.application_standards),
            "zinc presence/type": (
                self.zinc_present, _normalise(self.zinc_type)),
            "internal finish": _normalise(self.internal_finish),
            "external finish": _normalise(self.external_finish),
        }


def specification_for(order):
    """Return the exact certificate-relevant configuration for ``order``.

    Standards are read directly from the order snapshot and retain the
    configured Application order.  Missing legacy snapshots use the documented
    paper-form fallback and are explicitly marked so callers can warn and log.
    """
    stored_profile = getattr(order, "application_profile", None)
    used_fallback = not stored_profile
    standards = tuple(standard_labels(stored_profile))
    used_fallback = used_fallback or not standards
    if used_fallback:
        standards = LEGACY_APPLICATION_STANDARDS

    # Application profiles contain the selected standard and numeric wall,
    # cement and coating thickness bands.  They do not store the material or
    # finish names printed on a certificate.  Those named claims have one
    # authoritative source in the current schema: the Product parameter
    # relationships below.  Numeric layer differences therefore do not make
    # two orders incompatible unless they change a claim the certificate
    # actually prints; effective_application() above still resolves the exact
    # standard through the product-backed profile.
    product = getattr(order, "product", None)
    zinc_present, zinc_type = _zinc_value(
        getattr(product, "zinc_type_param", None) if product else None)
    internal_finish = _parameter_name(
        getattr(product, "internal_finish_param", None) if product else None)
    external_finish = _parameter_name(
        getattr(product, "external_finish_param", None) if product else None)

    return CertificateSpecification(
        application_standards=standards,
        zinc_present=zinc_present,
        zinc_type=zinc_type,
        internal_finish=internal_finish,
        external_finish=external_finish,
        used_application_fallback=used_fallback,
    )


def compatibility_conflicts(orders):
    """Describe certificate-relevant differences, naming orders and fields."""
    orders = list(orders)
    if len(orders) < 2:
        return []

    baseline = orders[0]
    baseline_values = specification_for(baseline).compatibility_values()
    conflicts = []
    for order in orders[1:]:
        values = specification_for(order).compatibility_values()
        fields = [field for field, expected in baseline_values.items()
                  if values[field] != expected]
        if fields:
            conflicts.append(
                f"{baseline.order_number} vs {order.order_number}: "
                + ", ".join(fields))
    return conflicts


def fallback_orders(orders):
    """Orders whose missing Application data required the legacy fallback."""
    return [order for order in orders
            if specification_for(order).used_application_fallback]
