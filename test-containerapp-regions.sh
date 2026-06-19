#!/usr/bin/env bash
#
# test-containerapp-regions.sh
# ---------------------------------------------------------------------------
# Tests the ability to create an Azure Container App Environment in ALL
# regions that support Microsoft.App/managedEnvironments, in order to
# identify those affected by the AKS capacity error:
#
#   ManagedEnvironmentCapacityHeavyUsageError / AKSCapacityHeavyUsage
#
# For each region: creates a resource group + a test Container App
# Environment (Consumption, no logs), then DELETES it immediately
# (self-cleaning). A final report classifies regions by state.
#
# Requirements:
#   - Recent Azure CLI (>= 2.49, supports --logs-destination none)
#   - Be logged in: az login
#
# Usage:
#   ./test-containerapp-regions.sh                 # test all regions
#   ./test-containerapp-regions.sh westeurope northeurope   # custom list
#   CONCURRENCY=10 ./test-containerapp-regions.sh  # 10 regions in parallel
#   KEEP=true ./test-containerapp-regions.sh       # do not delete (debug)
#   ./test-containerapp-regions.sh --cleanup       # delete the test RGs
# ---------------------------------------------------------------------------
set -uo pipefail

# --- Configurable parameters (via environment variables) -------------------
PREFIX="${PREFIX:-catest}"          # prefix for test resources
CONCURRENCY="${CONCURRENCY:-6}"     # number of regions tested in parallel
KEEP="${KEEP:-false}"               # true = do not delete the resources
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-900}"   # cap per region (15 min)
RESULTS_FILE="${RESULTS_FILE:-containerapp-capacity-results.csv}"

# --- Preliminary checks ----------------------------------------------------
command -v az >/dev/null 2>&1 || { echo "ERROR: Azure CLI (az) not found." >&2; exit 1; }
az account show >/dev/null 2>&1 || { echo "ERROR: not logged in. Run: az login" >&2; exit 1; }

# --- Cleanup mode: delete all test resource groups -------------------------
if [[ "${1:-}" == "--cleanup" ]]; then
  echo "Deleting resource groups starting with '${PREFIX}-'..."
  mapfile -t rgs < <(az group list --query "[?starts_with(name, '${PREFIX}-')].name" -o tsv)
  if [[ ${#rgs[@]} -eq 0 ]]; then
    echo "No test resource group found."
    exit 0
  fi
  for rg in "${rgs[@]}"; do
    echo "  -> deleting $rg"
    az group delete --name "$rg" --yes --no-wait --only-show-errors >/dev/null 2>&1 || true
  done
  echo "Deletions started (in the background)."
  exit 0
fi

# --- Ensure the required providers are registered --------------------------
state=$(az provider show --namespace Microsoft.App --query registrationState -o tsv 2>/dev/null || echo "")
if [[ "$state" != "Registered" ]]; then
  echo "Registering the Microsoft.App provider (may take a moment)..."
  az provider register --namespace Microsoft.App --only-show-errors >/dev/null 2>&1 || true
fi

# --- Retrieve the regions that support Container Apps ----------------------
list_supported_regions() {
  local supported
  supported=$(az provider show --namespace Microsoft.App \
      --query "resourceTypes[?resourceType=='managedEnvironments'].locations | [0]" \
      -o tsv 2>/dev/null)
  [[ -z "$supported" ]] && { echo "ERROR: unable to list supported regions." >&2; return 1; }

  # Convert display name -> short name, keeping only physical regions.
  az account list-locations \
      --query "[?metadata.regionType=='Physical'].[displayName, name]" -o tsv 2>/dev/null \
  | while IFS=$'\t' read -r display name; do
      grep -qxF -- "$display" <<<"$supported" && printf '%s\n' "$name"
    done | sort -u
}

# Region list: provided arguments, otherwise automatic discovery.
if [[ $# -gt 0 ]]; then
  mapfile -t REGIONS < <(printf '%s\n' "$@" | sort -u)
else
  echo "Discovering regions that support Container Apps..."
  mapfile -t REGIONS < <(list_supported_regions)
fi

[[ ${#REGIONS[@]} -eq 0 ]] && { echo "ERROR: no region to test." >&2; exit 1; }

# --- Temporary directory for per-region results ----------------------------
RESULTS_DIR="$(mktemp -d -t catest-XXXXXX)"
trap 'rm -rf "$RESULTS_DIR"' EXIT

echo "==============================================================="
echo "Regions to test    : ${#REGIONS[@]}"
echo "Parallelism        : $CONCURRENCY"
echo "Resource prefix    : $PREFIX"
echo "Keep (KEEP)        : $KEEP"
echo "==============================================================="
echo

# --- Function to test one region (run in parallel) -------------------------
test_one_region() {
  local region="$1"
  local rg="${PREFIX}-${region}"
  local env="${PREFIX}-env-${region}"
  local out rc status detail

  # Resource group dedicated to the region.
  if ! az group create --name "$rg" --location "$region" \
        --only-show-errors >/dev/null 2>&1; then
    printf 'ERROR|%s|resource group creation failed\n' "$region" \
      > "$RESULTS_DIR/$region.result"
    echo "[ERR ] $region (resource group)" >&2
    return
  fi

  # Attempt to create the Container App Environment (the operation that
  # triggers the capacity error when applicable).
  out=$(timeout "${TIMEOUT_SECONDS}s" az containerapp env create \
          --name "$env" \
          --resource-group "$rg" \
          --location "$region" \
          --logs-destination none \
          --only-show-errors 2>&1)
  rc=$?

  # Cleanup (unless KEEP=true).
  if [[ "$KEEP" != "true" ]]; then
    az group delete --name "$rg" --yes --no-wait --only-show-errors >/dev/null 2>&1 || true
  fi

  # Classify the result.
  if [[ $rc -eq 0 ]]; then
    status="OK"; detail="capacity available"
  elif [[ $rc -eq 124 ]]; then
    status="TIMEOUT"; detail="exceeded ${TIMEOUT_SECONDS}s"
  elif grep -qiE "AKSCapacityHeavyUsage|CapacityHeavyUsage|ManagedEnvironmentCapacity" <<<"$out"; then
    status="CAPACITY"; detail="capacity exhausted (AKSCapacityHeavyUsage)"
  else
    status="ERROR"
    detail=$(printf '%s' "$out" | tr '\n\r' '  ' | sed 's/  */ /g' | cut -c1-200)
  fi

  printf '%s|%s|%s\n' "$status" "$region" "$detail" > "$RESULTS_DIR/$region.result"
  echo "[$(printf '%-8s' "$status")] $region" >&2
}
export -f test_one_region
export PREFIX RESULTS_DIR KEEP TIMEOUT_SECONDS

# --- Parallel execution ----------------------------------------------------
echo "Tests running (each region takes a few minutes)..."
echo
printf '%s\n' "${REGIONS[@]}" \
  | xargs -P "$CONCURRENCY" -I{} bash -c 'test_one_region "$@"' _ {}

# --- Aggregation and report ------------------------------------------------
all=$(cat "$RESULTS_DIR"/*.result 2>/dev/null | sort)

echo "status,region,detail" > "$RESULTS_FILE"
while IFS='|' read -r st rg detail; do
  [[ -z "$st" ]] && continue
  printf '"%s","%s","%s"\n' "$st" "$rg" "$detail" >> "$RESULTS_FILE"
done <<<"$all"

count() { awk -F'|' -v s="$1" '$1==s{n++} END{print n+0}' <<<"$all"; }

echo
echo "============================ SUMMARY ==========================="
echo "OK (capacity available)  : $(count OK)"
echo "CAPACITY (exhausted)     : $(count CAPACITY)"
echo "ERROR (other error)      : $(count ERROR)"
echo "TIMEOUT                  : $(count TIMEOUT)"
echo "==============================================================="

print_group() {
  local label="$1" key="$2"
  local lines
  lines=$(awk -F'|' -v s="$key" '$1==s{print}' <<<"$all")
  [[ -z "$lines" ]] && return
  echo
  echo "-- $label --"
  while IFS='|' read -r st rg detail; do
    printf '  %-22s %s\n' "$rg" "$detail"
  done <<<"$lines"
}

print_group "OK regions (to use)" OK
print_group "Saturated regions (avoid)" CAPACITY
print_group "Regions in error" ERROR
print_group "Timed-out regions" TIMEOUT

echo
echo "Detailed results written to: $RESULTS_FILE"
if [[ "$KEEP" == "true" ]]; then
  echo "WARNING: KEEP=true, remember to run: $0 --cleanup"
fi

# The script ran to completion: exit 0 even if some regions are in
# CAPACITY/ERROR (those are results, not script failures).
exit 0
