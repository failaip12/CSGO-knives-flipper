<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'

interface Knife {
  knife_id: number
  knife_name: string
  current_min_price_with_fee: number | null
  current_min_price_without_fee: number | null
  last_min_price_with_fee: number
  last_min_price_without_fee: number
  buy_order_price: number
  last_updated: string
  last_sold: string
  amount_sold: number | null
  selling_frequency: number | null
  amount_sold_last_year: number
  knife_image: string
}

const knives = ref<Knife[]>([])
const page = ref(1)
const perPage = ref(50)
const maxBuyOrderPrice = ref<number | null>(null)
const minProfit = ref<number | null>(null)
const minSold = ref<number | null>(null)

onMounted(async () => {
  try {
    const response = await fetch('http://localhost:8000/knives')
    knives.value = await response.json()
  } catch (error) {
    console.error('Fetch error:', error)
  }
  knives.value.forEach((element) => console.log(element.knife_image))
})

const filteredKnives = computed(() => {
  let filtered = knives.value

  if (maxBuyOrderPrice.value !== null) {
    filtered = filtered.filter((knife) => knife.buy_order_price <= maxBuyOrderPrice.value!)
  }

  if (minProfit.value !== null) {
    filtered = filtered.filter((knife) => {
      const profit = knife.last_min_price_without_fee - knife.buy_order_price
      return profit >= minProfit.value!
    })
  }
  if (minSold.value !== null) {
    filtered = filtered.filter((knife) => knife.amount_sold_last_year >= minSold.value!)
  }
  filtered.sort((a, b) => {
    const profitA = a.last_min_price_without_fee - a.buy_order_price
    const profitB = b.last_min_price_without_fee - b.buy_order_price
    return profitB - profitA
  })

  return filtered
})

const displayedKnives = computed(() => {
  const startIndex = (page.value - 1) * perPage.value
  const endIndex = startIndex + perPage.value
  return filteredKnives.value.slice(startIndex, endIndex)
})

function nextPage() {
  if (page.value < totalPages.value) {
    page.value++
  }
}

function prevPage() {
  if (page.value > 1) {
    page.value--
  }
}
const totalPages = computed(() => Math.ceil(knives.value.length / perPage.value))
</script>

<template>
  <div class="sidebar-wrapper">
    <div class="navbar-logo">
      <div class="nav1">
        <img class="img1" src="/src/assets/knives-logo.png" />
      </div>
    </div>
    <form class="filter-form">
      <h3 class="form-title">Filter Knives</h3>
      <div class="form-group">
        <label for="maxBuyOrderPrice">Max Buy Order Price:</label>
        <input
          type="number"
          id="maxBuyOrderPrice"
          v-model.number="maxBuyOrderPrice"
          placeholder="Enter max price"
          class="form-input"
        />
      </div>
      <div class="form-group">
        <label for="minProfit">Min Profit:</label>
        <input
          type="number"
          id="minProfit"
          v-model.number="minProfit"
          placeholder="Enter min profit"
          class="form-input"
        />
      </div>
      <div class="form-group">
        <label for="minAmountSold">Min Amount Sold Last Year:</label>
        <input
          type="number"
          id="minSold"
          v-model.number="minSold"
          placeholder="Enter min sold"
          class="form-input"
        />
      </div>
      <div class="form-buttons">
        <button
          type="reset"
          class="form-button reset-button"
          @click="
            () => {
              maxBuyOrderPrice = null
              minProfit = null
              page = 1
            }
          "
        >
          Reset
        </button>
      </div>
    </form>
  </div>
  <div class="page-wrapper">
    <div class="tabela">
      <div class="table-container">
        <table class="product-table">
          <thead>
            <tr>
              <th>IMG</th>
              <th>Knife Name</th>
              <th>Knife Amount Sold</th>
              <th>AmountSoldLastYear</th>
              <th>BuyOrderPrice</th>
              <th>Profit</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="knife in displayedKnives" :key="knife.knife_id">
              <td><img style="width: 5rem" :src="knife.knife_image" loading="lazy" /></td>
              <td>
                <a :href="'https://steamcommunity.com/market/listings/730/' + knife.knife_name">{{
                  knife.knife_name
                }}</a>
              </td>
              <td>{{ knife.amount_sold ?? 'N/A' }}</td>
              <td class="price">{{ knife.amount_sold_last_year }}</td>
              <td class="price">€{{ knife.buy_order_price }}</td>
              <td class="price">€{{ knife.last_min_price_without_fee - knife.buy_order_price }}</td>
            </tr>
          </tbody>
        </table>
        <div class="pagination">
          <div>
            Showing {{ (page - 1) * perPage + 1 }} to
            {{ Math.min(page * perPage, knives.length) }} of {{ filteredKnives.length }} Results
          </div>
          <div class="pagination-controls">
            <button @click="prevPage" :disabled="page === 1">Prev</button>
            <button @click="nextPage" :disabled="page === totalPages">Next</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
<style>
.sidebar-wrapper {
  height: 100%;
  width: 15rem;
  position: fixed;
  z-index: 1;
  top: 0;
  left: 0;
  background-color: #111;
  overflow-x: hidden;
  padding-top: 20px;
  display: flex;
  flex-flow: column;
  gap: 160px;
}
.navbar-logo {
  display: flex;
  justify-content: center;
  align-content: center;
  width: 15rem;
}
.nav1 {
  display: inline-flex;
  justify-content: center;
  align-content: center;
  width: 100%;
}
.img1 {
  width: 100%;
  height: 100%;
  scale: 2;
}
.page-wrapper {
  margin-left: 14.5rem;
  height: 1000px;
  display: flex;
  flex-flow: column;
}

.table-container {
  font-family: 'Arial', sans-serif;
  margin: 20px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.12);
}

.product-table {
  width: 100%;
  border-collapse: collapse;
  background-color: white;
}

.product-table th {
  background-color: #f8f9fa;
  padding: 12px 15px;
  text-align: left;
  border-bottom: 2px solid #dee2e6;
  font-weight: 600;
}

.product-table td {
  padding: 12px 15px;
  border-bottom: 1px solid #dee2e6;
}

.status-badge {
  display: inline-block;
  padding: 4px 8px;
  border-radius: 12px;
  font-size: 0.85em;
}

.low-stock {
  background-color: #fff3cd;
  color: #856404;
}

.spock {
  background-color: #d4edda;
  color: #155724;
}

.out-of-stock {
  background-color: #f8d7da;
  color: #721c24;
}

.price {
  color: #2a5934;
  font-weight: bold;
}

.pagination {
  display: flex;
  justify-content: space-between;
  padding: 15px;
  background-color: #f8f9fa;
}

.pagination-controls {
  gap: 10px;
  display: flex;
}

.pagination-controls button {
  padding: 5px 10px;
  background-color: white;
  border: 1px solid #dee2e6;
  border-radius: 4px;
  cursor: pointer;
}

tr:hover {
  background-color: #f8f9fa;
}

.filter-form {
  display: flex;
  flex-flow: column;
  padding: 20px;
  background-color: #34495e;
  border-radius: 8px;
  margin: 20px;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.1);
}

.form-title {
  color: #ecf0f1;
  font-size: 1.2rem;
  margin-bottom: 20px;
  text-align: center;
}

.form-group {
  margin-bottom: 15px;
}

.form-group label {
  display: block;
  color: #bdc3c7;
  font-size: 0.9rem;
  margin-bottom: 5px;
}

.form-input {
  width: 100%;
  padding: 8px;
  border: 1px solid #7f8c8d;
  border-radius: 4px;
  background-color: #2c3e50;
  color: #ecf0f1;
  font-size: 0.9rem;
}

.form-input::placeholder {
  color: #7f8c8d;
}

.form-buttons {
  display: flex;
  gap: 10px;
  margin-top: 20px;
}

.form-button {
  flex: 1;
  padding: 10px;
  border: none;
  border-radius: 4px;
  font-size: 0.9rem;
  cursor: pointer;
  transition: background-color 0.3s ease;
}

.reset-button {
  background-color: #e74c3c;
  color: white;
}

.reset-button:hover {
  background-color: #c0392b;
}
</style>
