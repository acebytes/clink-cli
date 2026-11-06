"""Keep candidate family admissions separate from source-bound generation transport."""

from clink.native_generation_policy import NativeGenerationAdmission

DEFAULT_AGY_NATIVE_ADMISSION = NativeGenerationAdmission()
DEFAULT_GROK_NATIVE_ADMISSION = NativeGenerationAdmission(
    verified=True,
    version="grok 1.0.70 (f50904eaee6c)",
    executable_sha256="5c8a688251bf61e35370b828b6a353b0a20fcc3f64000de8a91d426039f29cec",
    recipe_sha256="0bc8f09a9a4f068ba3e3b690e8dca9437db1b209f0de370bbd66eb52d49fadf1",
    parser_sha256="9effe99b6ebe6e9f5ec6276ed0a149b7b8a2920263f4523ac50978c7349f15f3",
    evidence_path="clink/resources/native-generation-grok-1.0.70-admission.json",
    evidence_sha256="b7ec35215ce042ba4b128e4ec003a71a33e71d8bd825545e3fce85a365f49df5",
)
