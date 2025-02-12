<script setup lang="ts"></script>

<template>
  <div class="sidebar-wrapper">
    <div class="navbar-logo">
      <div class="nav1">
        <img class="img1" src="/src/assets/knives-logo.png" />
      </div>
    </div>
  </div>
  <div class="page-wrapper">
    <div class="tabela">
      <div class="table-container">
        <table class="product-table">
          <thead>
            <tr>
              <th>Product Code</th>
              <th>Item</th>
              <th>City Left</th>
              <th>Status</th>
              <th>Price</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>PEP-1478</td>
              <td>Blouse Ruffle Tube top</td>
              <td>145</td>
              <td><span class="status-badge low-stock">Low Stock</span></td>
              <td class="price">$14.99</td>
            </tr>
            <tr>
              <td>PEP-1479</td>
              <td>Gold-colored locket watch</td>
              <td>569</td>
              <td><span class="status-badge spock">Spock</span></td>
              <td class="price">$19.99</td>
            </tr>
            <tr>
              <td>PEP-1480</td>
              <td>Crop top Sweater Clothing</td>
              <td>541</td>
              <td><span class="status-badge spock">Spock</span></td>
              <td class="price">$22.49</td>
            </tr>
            <tr>
              <td>PEP-1481</td>
              <td>Sleeve Clothing Leggings</td>
              <td>126</td>
              <td><span class="status-badge low-stock">Low Stock</span></td>
              <td class="price">$31.78</td>
            </tr>
            <tr>
              <td>PEP-1482</td>
              <td>Yellow women shoes</td>
              <td>-</td>
              <td><span class="status-badge out-of-stock">Out of Stock</span></td>
              <td class="price">$49.99</td>
            </tr>
            <tr>
              <td>PEP-1483</td>
              <td>Straw hat Cap Cowboy</td>
              <td>571</td>
              <td><span class="status-badge spock">Spock</span></td>
              <td class="price">$49.99</td>
            </tr>
            <tr>
              <td>PEP-1484</td>
              <td>Sneakers Shoe Nike Basketball</td>
              <td>-</td>
              <td><span class="status-badge out-of-stock">Out of Stock</span></td>
              <td class="price">$49.99</td>
            </tr>
            <tr>
              <td>PEP-1485</td>
              <td>Modern Fashion T shirt</td>
              <td>321</td>
              <td><span class="status-badge spock">Spock</span></td>
              <td class="price">$29.49</td>
            </tr>
          </tbody>
        </table>
        <div class="pagination">
          <div>Showing 1-8 of 12 Results</div>
          <div class="pagination-controls">
            <button>Prev</button>
            <button>Next></button>
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
}
.navbar-logo {
  display: flex;
  justify-content: center;
  align-content: center;
  height: 4.68rem;
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
</style>
