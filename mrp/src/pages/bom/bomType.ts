// Display-only bom_type classification for BOM Explorer tree nodes.
// `ExplodeNode.bom_type` (the wire field) is only populated by mdm-api when
// a node owns its own approved BOM (milling/drymix/packaging) — see
// bom_explode.py's `explode_bom`: `node.bom_type = selected.bom_type` runs
// only inside the `if selected is not None` branch. A genuine leaf (raw
// material or purchased packaging item) never owns a BOM, so its wire
// `bom_type` is always null, even though the design spec's badge legend
// (§6.6: "milling / drymix / packaging / raw / packaging-material") expects
// every node to read as one of five buckets. This derives the missing two
// leaf-only buckets from the material code prefix, mirroring mdm-api's own
// `_bom_type()`/`expected_bom_type()` prefix table (nc_bom_sync/transform.py)
// exactly for the three it already covers, and adding CR->raw, CP->packaging-
// material for the two the backend has no reason to classify (those prefixes
// never own a BOM, so the backend's table only needs to answer "does this
// codeexpect a BOM" (missing_bom), not "what kind of leaf is this").
export function displayBomType(
  materialCode: string, wireBomType: string | null, name?: string | null,
): string | null {
  // Pasteurization (business, 2026-08-06): any material whose NAME contains
  // "Pasteurized" is a pasteurization intermediate — e.g. CR0059 "Pasteurized
  // Milk", CR0061/CR0181 etc., which own their own BOM but carry a CR prefix
  // so mdm-api's prefix table stores their bom_type as 'unknown'. Name-based,
  // so it must win over both the wire type and the prefix fallbacks below.
  // (The name is only on the wire for nodes mdm-api resolved; absent -> skip.)
  if (name && /pasteuri[sz]/i.test(name)) return 'past'
  const code = materialCode.toUpperCase()
  // CS-prefix semi-products: mdm-api stores bom_type 'milling', but the
  // business calls this step "Powdering" (制粉). Relabel at display.
  if (wireBomType === 'milling' || code.startsWith('CS')) return 'powdering'
  if (wireBomType) return wireBomType
  if (code.startsWith('CR')) return 'raw'
  if (code.startsWith('CP')) return 'packaging-material'
  return null
}
