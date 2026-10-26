"""Preflight — pay-per-audit continuity checking as a service.

upload series → token count → quote → PayPal order → buyer approves → capture → audit → report

Built for the PayPal AI Hackathon 2026. The payment layer is PayPal Orders v2 in the sandbox;
`FakePayPal` stands in for it in tests and the offline demo.
"""
