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
export function displayBomType(materialCode: string, wireBomType: string | null): string | null {
  if (wireBomType) return wireBomType
  const code = materialCode.toUpperCase()
  if (code.startsWith('CR')) return 'raw'
  if (code.startsWith('CP')) return 'packaging-material'
  return null
}
